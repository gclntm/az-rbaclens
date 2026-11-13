"""The assessment report: what matters in a scope, mapped to ISO/IEC 27001:2022.

Built on demand from what RBAC Lens already holds: the current grants and
their advice (core/advice.py), responsible people, review rounds, to-dos,
the changes feed and how fresh the data is. Control statuses follow fixed
thresholds (core/report_controls.py), so the same data always gives the
same report. A scope is the whole tenant, a management group or a
subscription; it holds the grants given there or inside.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.advice import AdviceContext, advise_grant
from app.core.advice import context as advice_context
from app.core.advice_texts import ORDER, RULES, WEIGHT_RANK
from app.core.changes import GIVEN_KINDS
from app.core.current_view import CurrentView, current_view, grant_id, owner_of
from app.core.findings import BROAD_LEVELS
from app.core.report_controls import evaluate
from app.core.reviews import period_of, previous_period
from app.core.timeutil import as_utc
from app.db.models import (
    AccessChange,
    ActivityPull,
    JobSchedule,
    ReviewItem,
    ReviewRound,
    RoleAssignment,
    Scan,
    Todo,
)
from app.schemas.advice import Advice
from app.schemas.report import (
    Report,
    ReportChanges,
    ReportGrant,
    ReportRecommendation,
    ReportReviews,
)

_KIND = {
    "User": "person",
    "Group": "group",
    "ForeignGroup": "group",
    "ServicePrincipal": "app",
    "MSI": "app",
    "Application": "app",
}
_SCOPE_RANK = {"mgmt_group": 0, "subscription": 1, "resource_group": 2, "resource": 3}
CHANGE_DAYS = 30
# Grants sent per recommendation: every must-do (capped), enough should-do to group in the PDF.
# The screen shows the first 5; the CSV annex always has them all.
SAMPLE = {"must": 50, "should": 30}
SAMPLE_OTHER = 5

# One sentence per rule for the summary and the recommendations: (one grant, {n} grants).
SENTENCES: dict[str, tuple[str, str]] = {
    "R01 orphan": (
        "1 grant belongs to an identity that no longer exists in Entra ID; remove it.",
        "{n} grants belong to identities that no longer exist in Entra ID; remove them.",
    ),
    "R02 standing-admin": (
        (
            "1 standing grant can change who has access on a management group or subscription; "
            "make it PIM-eligible."
        ),
        (
            "{n} standing grants can change who has access on a management group or subscription; "
            "make them PIM-eligible."
        ),
    ),
    "R03 idle-admin": (
        "1 grant can change who has access high up and was not used in 90 days; remove it.",
        "{n} grants can change who has access high up and were not used in 90 days; remove them.",
    ),
    "R04 guest-power": (
        "1 grant gives a guest from another organisation the power to change things; ask the sponsor.",
        (
            "{n} grants give guests from other organisations the power to change things; "
            "ask their sponsors."
        ),
    ),
    "R05 keys-broad": (
        "1 standing grant lets someone read keys and passwords high up; narrow it or use PIM.",
        "{n} standing grants let people read keys and passwords high up; narrow them or use PIM.",
    ),
    "R06 over-broad": (
        "1 grant is larger than what was used; replace it with a smaller role.",
        "{n} grants are larger than what was used; replace them with smaller roles.",
    ),
    "R07 redundant": (
        "1 grant is already covered from above; remove it.",
        "{n} grants are already covered from above; remove them.",
    ),
    "R08 direct-user": (
        "1 direct grant duplicates what a group already gives; remove it.",
        "{n} direct grants duplicate what a group already gives; remove them.",
    ),
    "R09 app-owner": (
        "1 grant lets an app change who has access; replace it with the least role the app needs.",
        "{n} grants let apps change who has access; replace them with the least role each app needs.",
    ),
    "R10 custom-replaceable": (
        "1 grant uses a custom role a built-in role could replace.",
        "{n} grants use custom roles a built-in role could replace.",
    ),
    "R11 unknown-members": (
        "1 grant goes to a group whose members can't be read; find out who is in it.",
        "{n} grants go to groups whose members can't be read; find out who is in them.",
    ),
    "R12 new-unexplained": (
        "1 recent grant has no known reason or giver; confirm it.",
        "{n} recent grants have no known reason or giver; confirm them.",
    ),
    "R14 expiring": (
        "1 grant ends within two weeks; extend it only on purpose.",
        "{n} grants end within two weeks; extend them only on purpose.",
    ),
}


def _how(ra: RoleAssignment) -> str:
    if ra.assignment_type == "Activated":
        return "pim"
    return "time-bound" if ra.end_at is not None else "standing"


def _grant(view: CurrentView, ra: RoleAssignment, adv: Advice) -> ReportGrant:
    return ReportGrant(
        grant_id=grant_id(ra),
        principal_id=ra.principal_id.lower(),
        principal_name=ra.principal_name,
        kind=_KIND.get(ra.principal_type, "other"),
        role_name=ra.role_name,
        level=view.capability_of(ra).level,
        scope=ra.scope,
        scope_name=view.name.get(ra.scope.lower()) or ra.scope_name,
        scope_level=ra.scope_level,
        how=_how(ra),
        advice_rule=adv.rule,
        advice_title=adv.title,
        advice_weight=adv.weight,
    )


def _broad_first(g: ReportGrant) -> tuple[int, str, str]:
    return (
        _SCOPE_RANK.get(g.scope_level, 9),
        (g.scope_name or g.scope).lower(),
        (g.principal_name or "").lower(),
    )


class ReportScope:
    """Which places, grants and rows belong to the report's scope."""

    def __init__(self, view: CurrentView, scope_id: str | None) -> None:
        self.view = view
        self.id = scope_id
        self.inside = view.subtree(scope_id) if scope_id else set(view.pointer)
        self.known = set(view.pointer)

    def has(self, scope: str) -> bool:
        return self.id is None or owner_of(scope, self.known) in self.inside

    @property
    def subscriptions(self) -> list[str]:
        return sorted(s for s in self.inside if self.view.level.get(s) == "subscription")


def build_report(session: Session, scope_id: str | None, now: datetime | None = None) -> Report:
    now = now or datetime.now(UTC)
    view = current_view(session)
    ctx = advice_context(session, view)
    sc = ReportScope(view, scope_id)
    ras = [ra for ra in view.ras if sc.has(ra.scope)]
    ctx.prime_groups({ra.principal_id.lower() for ra in ras if ra.principal_type == "User"})
    advised = [(ra, advise_grant(ctx, ra)) for ra in ras]

    privileged = sorted(
        (
            _grant(view, ra, adv)
            for ra, adv in advised
            if view.capability_of(ra).level in ("access", "keys")
            and ra.scope_level in BROAD_LEVELS
            and adv.rule != "R16 microsoft-app"
        ),
        key=_broad_first,
    )
    by_rule: dict[str, list[ReportGrant]] = defaultdict(list)
    for ra, adv in advised:
        by_rule[adv.rule].append(_grant(view, ra, adv))
    recommendations = _recommendations(by_rule)
    reviews = _reviews(session, sc, ctx, now)
    changes = _changes(session, sc, len(by_rule.get("R12 new-unexplained", [])), now)
    identities = {
        "orphans": sorted(by_rule.get("R01 orphan", []), key=_broad_first),
        "guests": sorted(by_rule.get("R04 guest-power", []), key=_broad_first),
        "unknown_members": sorted(by_rule.get("R11 unknown-members", []), key=_broad_first),
    }
    decided = _decided_fingerprints(session, now)
    numbers = _numbers(advised, by_rule, privileged, reviews, changes, view, ctx)
    controls = evaluate(numbers, by_rule, decided, reviews, changes)
    name = view.name.get(scope_id) if scope_id else None
    return Report(
        scope_id=scope_id,
        scope_name=name or (scope_id.rsplit("/", 1)[-1] if scope_id else "The whole tenant"),
        scope_level=view.level.get(scope_id) if scope_id else None,
        generated_at=now,
        access_as_of=_as_of(session, ras),
        identities=len({ra.principal_id.lower() for ra in ras}),
        grants=len(ras),
        summary=[r.sentence for r in recommendations if r.weight in ("must", "should")][:5],
        controls=controls,
        privileged=privileged,
        break_glass=sorted(ctx.settings.break_glass),
        microsoft_apps=len(by_rule.get("R16 microsoft-app", [])),
        identities_section=identities,
        least_privilege=[
            r
            for r in recommendations
            if r.rule.split(" ")[0] in ("R05", "R06", "R07", "R08", "R09", "R10")
        ],
        reviews=reviews,
        changes=changes,
        recommendations=recommendations,
        numbers=numbers,
    )


def _recommendations(by_rule: dict[str, list[ReportGrant]]) -> list[ReportRecommendation]:
    out = []
    for rule, grants in by_rule.items():
        if rule not in SENTENCES:
            continue
        r = RULES[rule]
        grants.sort(key=_broad_first)
        out.append(
            ReportRecommendation(
                rule=rule,
                weight=r.weight,
                title=r.title,
                sentence=SENTENCES[rule][0 if len(grants) == 1 else 1].format(n=len(grants)),
                count=len(grants),
                controls=list(r.controls),
                grants=grants[: SAMPLE.get(r.weight, SAMPLE_OTHER)],
            )
        )
    out.sort(key=lambda r: (WEIGHT_RANK[r.weight], -r.count, ORDER[r.rule]))
    return out


def _as_of(session: Session, ras: list[RoleAssignment]) -> datetime | None:
    scans = {ra.scan_id for ra in ras}
    return (
        as_utc(session.scalar(select(func.max(Scan.finished_at)).where(Scan.id.in_(scans))))
        if scans
        else None
    )


def _decided_fingerprints(session: Session, now: datetime) -> set[str]:
    """Grants with a remove/change/ask decision this month, or an open to-do: "under review"."""
    period = period_of(now)
    fps = set(
        session.scalars(
            select(ReviewItem.grant_fingerprint)
            .join(ReviewRound, ReviewRound.id == ReviewItem.round_id)
            .where(
                ReviewRound.period == period, ReviewItem.decision.in_(("remove", "change", "ask"))
            )
        ).all()
    )
    return fps | set(
        session.scalars(select(Todo.grant_fingerprint).where(Todo.status == "open")).all()
    )


def _reviews(session: Session, sc: ReportScope, ctx: AdviceContext, now: datetime) -> ReportReviews:
    subs = sc.subscriptions
    without = [ctx.view.name.get(s) or s for s in subs if s not in ctx.responsible]
    period = period_of(now)
    last = previous_period(period)

    def in_scope(r: ReviewRound) -> bool:
        return r.scope_id is None or sc.has(r.scope_id)

    rounds = [
        r
        for r in session.scalars(
            select(ReviewRound).where(ReviewRound.period.in_((period, last)))
        ).all()
        if in_scope(r)
    ]
    current = [r for r in rounds if r.period == period]
    earlier = [r for r in rounds if r.period == last]
    items = session.scalars(
        select(ReviewItem).where(ReviewItem.round_id.in_([r.id for r in current] or [-1]))
    ).all()
    items = [i for i in items if sc.has(i.scope)]
    on_time = (
        all(
            r.finished_at is not None and as_utc(r.finished_at) <= as_utc(r.due_at) for r in earlier
        )
        if earlier
        else None
    )
    todos = [t for t in session.scalars(select(Todo)).all() if sc.has(t.scope)]
    done = [t for t in todos if t.status == "done" and t.done_at]
    avg = (
        (
            sum((as_utc(t.done_at) - as_utc(t.created_at)).total_seconds() for t in done)
            / len(done)
            / 86400
        )
        if done
        else None
    )
    return ReportReviews(
        subscriptions=len(subs),
        subscriptions_with_responsible=len(subs) - len(without),
        without_responsible=without,
        rounds_this_period=len(current),
        items_this_period=len(items),
        decided_this_period=sum(1 for i in items if i.decision is not None),
        last_period=last if earlier else None,
        last_period_on_time=on_time,
        todos_open=sum(1 for t in todos if t.status == "open"),
        todos_overdue=sum(1 for t in todos if t.status == "open" and as_utc(t.due_at) < now),
        todos_done=len(done),
        todos_avg_days_to_close=round(avg, 1) if avg is not None else None,
    )


def _changes(session: Session, sc: ReportScope, unexplained: int, now: datetime) -> ReportChanges:
    since = now - timedelta(days=CHANGE_DAYS)
    rows = [
        r
        for r in session.scalars(
            select(AccessChange).where(AccessChange.occurred_at >= since)
        ).all()
        if sc.has(r.scope)
    ]
    given = [r for r in rows if r.kind in GIVEN_KINDS]
    daily = session.scalars(
        select(JobSchedule)
        .where(JobSchedule.kind == "snapshot", JobSchedule.at_hour.is_not(None))
        .order_by(JobSchedule.id)
        .limit(1)
    ).first()
    last_check = session.get(Scan, daily.last_job_id) if daily and daily.last_job_id else None
    check_at = (
        as_utc(last_check.finished_at) if last_check and last_check.status == "done" else None
    )
    if check_at is None:
        check_at = as_utc(
            session.scalar(select(func.max(Scan.finished_at)).where(Scan.status == "done"))
        )
    hourly = {
        s.target.lower().rsplit("/", 1)[-1]
        for s in session.scalars(
            select(JobSchedule).where(
                JobSchedule.kind == "activity_pull",
                JobSchedule.enabled.is_(True),
                JobSchedule.every_hours <= 1,
            )
        ).all()
    }
    newest = dict(
        session.execute(
            select(func.lower(ActivityPull.subscription_id), func.max(ActivityPull.end_time))
            .where(ActivityPull.status == "done")
            .group_by(func.lower(ActivityPull.subscription_id))
        ).all()
    )
    gaps: list[float] = []
    missing: list[str] = []
    collected = 0
    for sub in sc.subscriptions:
        guid = sub.rsplit("/", 1)[-1]
        end = as_utc(newest.get(guid))
        if guid in hourly:
            collected += 1
        if end is None:
            missing.append(sc.view.name.get(sub) or guid)
        else:
            gaps.append((now - end).total_seconds() / 3600)
    gap = None if missing and not gaps else (max(gaps) if gaps else None)
    return ReportChanges(
        days=CHANGE_DAYS,
        given=len(given),
        removed=len(rows) - len(given),
        powerful_given=sum(1 for r in given if r.level in ("access", "keys")),
        unexplained=unexplained,
        check_age_hours=round((now - check_at).total_seconds() / 3600, 1) if check_at else None,
        subscriptions_collected_hourly=collected,
        activity_gap_hours=round(gap, 1) if gap is not None else None,
        subscriptions_not_collected=missing,
    )


def _numbers(
    advised: list[tuple[RoleAssignment, Advice]],
    by_rule: dict[str, list[ReportGrant]],
    privileged: list[ReportGrant],
    reviews: ReportReviews,
    changes: ReportChanges,
    view: CurrentView,
    ctx: AdviceContext,
) -> dict[str, Any]:
    standing_admin = [
        g
        for g in privileged
        if g.how == "standing"
        and g.level == "access"
        and g.principal_id not in ctx.settings.break_glass
    ]
    keys_high = [g for g in privileged if g.how == "standing" and g.level == "keys"]
    lp = sum(
        len(by_rule.get(r, []))
        for r in ("R06 over-broad", "R07 redundant", "R08 direct-user", "R09 app-owner")
    )
    return {
        "grants": len(advised),
        "standing_admin_high": len(standing_admin),
        "standing_admin_guests": sum(
            1 for g in standing_admin if "#ext#" in (g.principal_name or "").lower()
        ),
        "keys_high": len(keys_high),
        "keys_on_mg": sum(1 for g in keys_high if g.scope_level == "mgmt_group"),
        "orphans": len(by_rule.get("R01 orphan", [])),
        "guests_with_power": len(by_rule.get("R04 guest-power", [])),
        "unknown_member_groups": len(
            {g.principal_id for g in by_rule.get("R11 unknown-members", [])}
        ),
        "least_privilege": lp,
        "must": sum(1 for _, a in advised if a.weight == "must"),
        "should": sum(1 for _, a in advised if a.weight == "should"),
        "subscriptions_without_responsible": len(reviews.without_responsible),
        "todos_overdue": reviews.todos_overdue,
        "check_age_hours": changes.check_age_hours,
        "activity_gap_hours": changes.activity_gap_hours,
    }
