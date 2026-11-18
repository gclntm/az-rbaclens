"""Monthly reviews: Keep / Remove / Ask for every grant, once a month.

Each month (container local time) a round opens for every place where a
responsible person is set, plus one admins' round. A grant belongs to the
round of the nearest responsible person at or above the place it was
given on, so a grant is reviewed only where it was given (inherited
grants are not reviewed again below). Nobody reviews their own access: a
grant to the reviewer, or to a group the reviewer is in, moves to the
next responsible person above, or to the admins.

A grant kept last month and unchanged since (no entry in the changes
feed) starts as "keep". Every undecided item carries the playbook's
suggested decision (core/advice.py), refreshed at each check; the reviewer
confirms it or not. "Change" keeps the access but changes how it is given
(PIM, a smaller role) and needs a note. RBAC Lens never changes Azure:
finishing a round turns "remove" and "change" into to-dos (core/todos.py)
that the next checks close.
"""

from __future__ import annotations

import calendar
import logging
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.advice import AdviceContext, advise_grant
from app.core.advice import context as advice_context
from app.core.advice_texts import delete_command, entra_delete_command
from app.core.current_view import CurrentView, current_view, grant_id, owner_of
from app.core.entra_roles import ENTRA_PLACE
from app.core.notify import enqueue
from app.core.responsible import Resolved, resolve_all
from app.core.timeutil import as_utc
from app.db.models import AccessChange, ReviewDecision, ReviewItem, ReviewRound, RoleAssignment

logger = logging.getLogger(__name__)

DECISIONS = ("keep", "remove", "change", "ask")
ASK_REMINDER = timedelta(days=7)
DUE_SOON = timedelta(days=3)
ADMINS = "Admins"


# ---- periods (container local time) ------------------------------------------------------


def period_of(now: datetime) -> str:
    local = datetime.fromtimestamp(now.timestamp())
    return f"{local.year:04d}-{local.month:02d}"


def previous_period(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def due_at(period: str) -> datetime:
    """The last second of the month, container local time, as UTC."""
    year, month = (int(x) for x in period.split("-"))
    last = calendar.monthrange(year, month)[1]
    return datetime(year, month, last, 23, 59, 59).astimezone(UTC)


# ---- routing -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    ra: RoleAssignment
    round_scope: str | None  # None = admins
    moved_from: str | None


def _owns(ra: RoleAssignment, person_id: str, groups: dict[str, set[str]]) -> bool:
    pid = ra.principal_id.lower()
    return pid == person_id or (
        ra.principal_type == "Group" and pid in groups.get(person_id, set())
    )


def route_grants(
    view: CurrentView, resolved: dict[str, Resolved], scopes: dict[str, Any]
) -> list[Route]:
    """Which round every current grant belongs to."""
    known = set(scopes)
    reviewers = {r.person.person_id for r in resolved.values()}
    groups = view.groups_of(reviewers)
    out: list[Route] = []
    for ra in view.ras:
        place = owner_of(ra.scope, known)
        r = resolved.get(place) if place else None
        moved: str | None = None
        while r is not None and _owns(ra, r.person.person_id, groups):
            moved = moved or r.from_scope
            parent = scopes[r.from_scope][2]
            r = resolved.get(parent) if parent else None
        out.append(Route(ra, r.from_scope if r else None, moved))
    return out


# ---- opening and keeping rounds current ----------------------------------------------------


def _carried(session: Session, period: str) -> dict[str, ReviewItem]:
    """Grants kept last month and unchanged since, by fingerprint."""
    kept = {
        i.grant_fingerprint: i
        for i in session.scalars(
            select(ReviewItem)
            .join(ReviewRound, ReviewRound.id == ReviewItem.round_id)
            .where(ReviewRound.period == previous_period(period), ReviewItem.decision == "keep")
        ).all()
    }
    if not kept:
        return {}
    last_change = {
        key: as_utc(at)
        for key, at in session.execute(
            select(AccessChange.subject_key, func.max(AccessChange.occurred_at))
            .where(AccessChange.subject_key.in_(list(kept)))
            .group_by(AccessChange.subject_key)
        ).all()
    }
    return {
        fp: i
        for fp, i in kept.items()
        if fp not in last_change
        or (i.decided_at is not None and last_change[fp] <= as_utc(i.decided_at))
    }


def _azure_ids(session: Session, ra_ids: list[int]) -> dict[int, str]:
    """Azure's assignment id per current grant (``raw`` is deferred in the view)."""
    out: dict[int, str] = {}
    for i in range(0, len(ra_ids), 500):
        out.update(
            {
                rid: aid
                for rid, aid in session.execute(
                    select(RoleAssignment.id, RoleAssignment.raw["id"].as_string()).where(
                        RoleAssignment.id.in_(ra_ids[i : i + 500])
                    )
                ).all()
                if aid
            }
        )
    return out


def open_and_sync(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Open this month's rounds and keep their items current. Idempotent. Commits.

    New grants join their open round; grants that disappeared are marked
    gone; a finished round takes no new items.
    """
    now = now or datetime.now(UTC)
    period = period_of(now)
    view = current_view(session)
    resolved, scopes = resolve_all(session)
    routes = route_grants(view, resolved, scopes)
    rounds = {
        r.scope_id: r
        for r in session.scalars(select(ReviewRound).where(ReviewRound.period == period)).all()
    }
    existing = {
        (i.round_id, i.grant_fingerprint): i
        for i in session.scalars(
            select(ReviewItem).where(
                ReviewItem.round_id.in_([r.id for r in rounds.values()] or [-1])
            )
        ).all()
    }
    carried = _carried(session, period)
    azure_ids = _azure_ids(session, [r.ra.id for r in routes])
    ctx = advice_context(session, view)
    ctx.prime_groups({r.ra.principal_id.lower() for r in routes if r.ra.principal_type == "User"})
    stats = {"rounds_opened": 0, "items_added": 0, "items_gone": 0}

    def round_for(scope: str | None) -> ReviewRound:
        if scope in rounds:
            return rounds[scope]
        person = resolved[scope].person if scope else None
        r = ReviewRound(
            scope_id=scope,
            period=period,
            opened_at=now,
            due_at=due_at(period),
            reviewer_id=person.person_id if person else None,
            reviewer_name=(person.person_name or person.person_email) if person else ADMINS,
        )
        session.add(r)
        session.flush()
        rounds[scope] = r
        stats["rounds_opened"] += 1
        enqueue(
            session,
            "review_opened",
            recipient_id=r.reviewer_id,
            recipient_name=r.reviewer_name,
            subject_ref=f"review:{r.id}",
            dedupe_key=f"review_opened:{r.id}",
            payload={
                "round_id": r.id,
                "period": period,
                "place": scopes[scope][0] if scope else None,
                "due_at": r.due_at.isoformat(),
                "path": f"/reviews/{r.id}",
            },
        )
        return r

    present: set[tuple[int, str]] = set()
    for route in routes:
        rnd = round_for(route.round_scope)
        fp = grant_id(route.ra)
        key = (rnd.id, fp)
        present.add(key)
        item = existing.get(key)
        if item is not None:
            item.gone_at = None
            if item.decision is None and rnd.finished_at is None:
                _suggest(item, ctx, route.ra)
            continue
        if rnd.finished_at is not None:
            continue
        ra = route.ra
        item = ReviewItem(
            round_id=rnd.id,
            grant_fingerprint=fp,
            scope=ra.scope.lower(),
            scope_name=ra.scope_name,
            scope_level=ra.scope_level,
            principal_id=ra.principal_id.lower(),
            principal_name=ra.principal_name,
            principal_type=ra.principal_type,
            role_definition_id=ra.role_definition_id,
            role_name=ra.role_name,
            level=view.capability_of(ra).level,
            azure_id=azure_ids.get(ra.id),
            moved_from_scope=route.moved_from,
            added_at=now,
        )
        _suggest(item, ctx, ra)
        prev = carried.get(fp)
        if prev is not None:
            item.decision = "keep"
            item.carried_from_round_id = prev.round_id
            item.decided_by_name = "kept last month, unchanged since"
            item.decided_at = now
        session.add(item)
        existing[key] = item
        stats["items_added"] += 1

    for key, item in existing.items():
        if key not in present and item.gone_at is None:
            item.gone_at = now
            stats["items_gone"] += 1
    for scope, rnd in rounds.items():
        r = resolved.get(scope) if scope else None
        if rnd.finished_at is None and r is not None and r.from_scope == scope:
            rnd.reviewer_id = r.person.person_id
            rnd.reviewer_name = r.person.person_name or r.person.person_email
    session.flush()
    stats["notifications"] = _due_and_reminders(session, list(rounds.values()), existing, now)
    from app.core.todos import close_done, remind_overdue

    stats["todos_done"] = close_done(session, view, now)
    stats["notifications"] += remind_overdue(session, now)
    session.commit()
    return stats


def _suggest(item: ReviewItem, ctx: AdviceContext, ra: RoleAssignment) -> None:
    """Copy the playbook's advice onto an undecided item."""
    adv = advise_grant(ctx, ra)
    item.suggested, item.suggested_rule, item.suggested_title = adv.decision, adv.rule, adv.title


def _due_and_reminders(
    session: Session,
    rounds: list[ReviewRound],
    items: dict[tuple[int, str], ReviewItem],
    now: datetime,
) -> int:
    """Queue "due soon" for open rounds near their end, and reminders for open questions."""
    queued = 0
    by_round: dict[int, list[ReviewItem]] = {}
    for (rid, _), item in items.items():
        by_round.setdefault(rid, []).append(item)
    for rnd in rounds:
        if rnd.finished_at is not None:
            continue
        left = blocking(by_round.get(rnd.id, []))
        if (
            left
            and as_utc(rnd.due_at) - now <= DUE_SOON
            and enqueue(
                session,
                "review_due",
                recipient_id=rnd.reviewer_id,
                recipient_name=rnd.reviewer_name,
                subject_ref=f"review:{rnd.id}",
                dedupe_key=f"review_due:{rnd.id}",
                payload={
                    "round_id": rnd.id,
                    "period": rnd.period,
                    "left": left,
                    "due_at": rnd.due_at.isoformat(),
                    "path": f"/reviews/{rnd.id}",
                },
            )
        ):
            queued += 1
        for item in by_round.get(rnd.id, []):
            if item.decision != "ask" or item.gone_at is not None or item.ask_remind_at is None:
                continue
            if as_utc(item.ask_remind_at) > now:
                continue
            if enqueue(
                session,
                "reminder",
                recipient_id=rnd.reviewer_id,
                recipient_name=rnd.reviewer_name,
                subject_ref=f"review:{rnd.id}/item:{item.id}",
                dedupe_key=f"reminder:{item.id}:{as_utc(item.ask_remind_at).date().isoformat()}",
                payload={
                    **_item_payload(item, rnd),
                    "asked_at": item.decided_at.isoformat() if item.decided_at else None,
                },
            ):
                queued += 1
            item.ask_remind_at = now + ASK_REMINDER
    return queued


def _item_payload(item: ReviewItem, rnd: ReviewRound) -> dict[str, Any]:
    return {
        "round_id": rnd.id,
        "item_id": item.id,
        "period": rnd.period,
        "who": item.principal_name or item.principal_id,
        "who_type": item.principal_type,
        "role": item.role_name,
        "place": item.scope_name or item.scope,
        "note": item.note,
        "reviewer": rnd.reviewer_name,
        "path": f"/reviews/{rnd.id}",
        "grant_path": f"/grants/{item.grant_fingerprint}",
    }


# ---- decisions -------------------------------------------------------------------------------


@dataclass
class Progress:
    total: int
    keep: int
    remove: int
    change: int
    ask: int
    open: int
    gone: int


def progress(items: Iterable[ReviewItem]) -> Progress:
    p = Progress(0, 0, 0, 0, 0, 0, 0)
    for i in items:
        p.total += 1
        if i.gone_at is not None and i.decision != "remove":
            p.gone += 1
        elif i.decision is None:
            p.open += 1
        else:
            setattr(p, i.decision, getattr(p, i.decision) + 1)
    return p


def blocking(items: Iterable[ReviewItem]) -> int:
    """Items that keep a round from being finished: undecided or still asked, grant still there."""
    return sum(1 for i in items if i.gone_at is None and i.decision in (None, "ask"))


def decide(
    session: Session,
    item: ReviewItem,
    decision: str | None,
    note: str | None,
    actor_id: str | None,
    actor_name: str | None,
    now: datetime | None = None,
) -> ReviewItem:
    """Record (or clear, with ``None``) a decision. The caller commits.

    Keeping a grant marks its open findings reviewed, so it leaves
    "Needs a look" too.
    """
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    if decision == "change" and not (note or "").strip():
        raise ValueError("say what to change: PIM, a smaller role, another place…")
    now = now or datetime.now(UTC)
    item.decision = decision
    item.note = note
    item.decided_by = actor_id
    item.decided_by_name = actor_name if decision else None
    item.decided_at = now if decision else None
    item.carried_from_round_id = None
    item.ask_remind_at = now + ASK_REMINDER if decision == "ask" else None
    if decision == "keep":
        _review_findings(session, item, actor_id, actor_name)
    if decision == "ask":
        rnd = session.get(ReviewRound, item.round_id)
        person = item.principal_type == "User"
        enqueue(
            session,
            "ask",
            recipient_id=item.principal_id if person else (rnd.reviewer_id if rnd else None),
            recipient_name=item.principal_name if person else (rnd.reviewer_name if rnd else None),
            subject_ref=f"review:{item.round_id}/item:{item.id}",
            dedupe_key=f"ask:{item.id}:{now.isoformat()}",
            payload={**(_item_payload(item, rnd) if rnd else {}), "to_the_person": person},
        )
    return item


def _review_findings(
    session: Session, item: ReviewItem, actor_id: str | None, actor_name: str | None
) -> None:
    view = current_view(session)
    ra = view.by_grant_id.get(item.grant_fingerprint)
    if ra is None:
        return
    for ref in view.findings_by_ra.get(ra.id, ()):
        if ref.status != "open":
            continue
        row = session.get(ReviewDecision, ref.finding.fingerprint) or ReviewDecision(
            fingerprint=ref.finding.fingerprint
        )
        row.decision = "reviewed"
        row.note = "Kept in the monthly review"
        row.snoozed_until = None
        row.actor = actor_id
        row.actor_name = actor_name
        session.add(row)


def accept_advice(
    session: Session,
    items: Iterable[ReviewItem],
    actor_id: str | None,
    actor_name: str | None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Confirm the suggested decision on every undecided item still there. The caller commits."""
    counts = dict.fromkeys(DECISIONS, 0)
    for item in items:
        if item.decision is not None or item.gone_at is not None or item.suggested not in DECISIONS:
            continue
        note = (
            f"As advised ({item.suggested_rule}): {item.suggested_title}"
            if item.suggested != "keep"
            else None
        )
        decide(session, item, item.suggested, note, actor_id, actor_name, now)
        counts[item.suggested] += 1
    return counts


def remove_command(item: ReviewItem) -> str:
    """A ready-to-run Azure CLI command; RBAC Lens never runs it."""
    if item.scope == ENTRA_PLACE:
        return entra_delete_command(
            item.azure_id, item.principal_id, item.role_name or item.role_definition_id
        )
    if item.azure_id:
        return f"az role assignment delete --ids {shlex.quote(item.azure_id)}"
    return delete_command(item.principal_id, item.role_name or item.role_definition_id, item.scope)
