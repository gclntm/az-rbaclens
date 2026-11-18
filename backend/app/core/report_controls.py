"""Control statuses for the assessment report: fixed thresholds, one function per control.

See docs/api.md (Report). Each control says met, partly met
or not met, with the facts it rests on, so an auditor can check the logic.
"""

from __future__ import annotations

from typing import Any

from app.core.advice_texts import CONTROLS
from app.schemas.report import (
    ControlStatus,
    ReportChanges,
    ReportControl,
    ReportGrant,
    ReportReviews,
)

ADMIN_PARTLY_MAX = 3  # 8.2: up to this many standing "change access" grants high up is "partly met"
LEAST_PRIVILEGE_PARTLY_MAX = 9  # 5.15
CHECK_FRESH_HOURS = 26  # 8.15: the daily check, with slack
ACTIVITY_FRESH_HOURS = 3  # 8.15: hourly collection, with slack
STALE_HOURS = 7 * 24  # 8.15: beyond this, "not met"


def _c(control: str, status: ControlStatus, evidence: list[str]) -> ReportControl:
    return ReportControl(control=control, name=CONTROLS[control], status=status, evidence=evidence)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def privileged(n: dict[str, Any]) -> ReportControl:
    admins, guests = n["standing_admin_high"], n["standing_admin_guests"]
    status: ControlStatus = (
        "met"
        if admins == 0
        else ("partly" if admins <= ADMIN_PARTLY_MAX and guests == 0 else "not_met")
    )
    evidence = [
        (
            f"{_plural(admins, 'standing grant')} can change who has access on a management group or "
            "subscription (break-glass identities and Microsoft's own apps not counted)."
        )
    ]
    if guests:
        evidence.append(
            f"{_plural(guests, 'of them goes', 'of them go')} to guests from other organisations."
        )
    evidence.append(
        f"{_plural(n['keys_high'], 'standing grant')} can read keys and passwords high up."
    )
    return _c("8.2", status, evidence)


def identities(n: dict[str, Any], orphans: list[ReportGrant], decided: set[str]) -> ReportControl:
    o, u = n["orphans"], n["unknown_member_groups"]
    loose = [g for g in orphans if g.grant_id not in decided]
    if o == 0 and u == 0:
        status: ControlStatus = "met"
    elif loose:
        status = "not_met"
    else:
        status = "partly"
    evidence = [
        f"{_plural(o, 'grant')} to identities that no longer exist in Entra ID"
        + (f", {len(loose)} not yet decided in a review." if o else "."),
        f"{_plural(u, 'group')} whose members can't be read.",
        f"{_plural(n['guests_with_power'], 'grant')} give guests the power to change things.",
    ]
    return _c("5.16", status, evidence)


def least_privilege(n: dict[str, Any]) -> ReportControl:
    lp = n["least_privilege"]
    status: ControlStatus = (
        "met" if lp == 0 else ("partly" if lp <= LEAST_PRIVILEGE_PARTLY_MAX else "not_met")
    )
    return _c(
        "5.15",
        status,
        [
            (
                f"{_plural(lp, 'grant')} larger than needed: too broad a role, already covered "
                "from above, duplicating a group, or an app that can change access."
            )
        ],
    )


def authentication(n: dict[str, Any]) -> ReportControl:
    k, on_mg = n["keys_high"], n["keys_on_mg"]
    status: ControlStatus = "met" if k == 0 else ("partly" if on_mg == 0 else "not_met")
    return _c(
        "5.17",
        status,
        [
            (
                f"{_plural(k, 'standing grant')} let identities read keys and passwords on a "
                f"subscription or management group, {on_mg} of them on a management group."
            )
        ],
    )


def access_rights(r: ReportReviews) -> ReportControl:
    gaps = [
        r.without_responsible != [],
        r.last_period_on_time is not True,
        r.todos_overdue > 0,
    ]
    missing = sum(gaps)
    status: ControlStatus = "met" if missing == 0 else ("partly" if missing == 1 else "not_met")
    on_time = {
        True: f"The {r.last_period} reviews were finished on time.",
        False: f"The {r.last_period} reviews were not all finished on time.",
        None: "No earlier month's review was finished yet.",
    }[r.last_period_on_time]
    return _c(
        "5.18",
        status,
        [
            f"{r.subscriptions_with_responsible} of {_plural(r.subscriptions, 'subscription')} have someone responsible.",
            on_time,
            f"{r.decided_this_period} of {_plural(r.items_this_period, 'grant')} decided in this month's reviews.",
            f"{_plural(r.todos_open, 'change')} to make in Azure, {r.todos_overdue} overdue.",
        ],
    )


def logging_control(c: ReportChanges, subscriptions: int) -> ReportControl:
    age, gap = c.check_age_hours, c.activity_gap_hours
    activity_ok = subscriptions == 0 or (
        gap is not None
        and gap <= ACTIVITY_FRESH_HOURS
        and c.subscriptions_collected_hourly == subscriptions
        and not c.subscriptions_not_collected
    )
    if age is not None and age <= CHECK_FRESH_HOURS and activity_ok:
        status: ControlStatus = "met"
    elif (
        age is not None
        and age <= STALE_HOURS
        and (subscriptions == 0 or (gap is not None and gap <= STALE_HOURS))
    ):
        status = "partly"
    else:
        status = "not_met"
    evidence = [
        f"The last check of every grant finished {age:.0f} hours ago."
        if age is not None
        else "No check has finished.",
        f"{c.subscriptions_collected_hourly} of {_plural(subscriptions, 'subscription')} collect the Activity Log "
        "every hour" + (f"; the oldest is {gap:.0f} hours behind." if gap is not None else "."),
        (
            f"{_plural(c.given, 'grant')} given and {c.removed} removed in {c.days} days, "
            f"{c.unexplained} without a known reason. Activity at management-group level is not collected."
        ),
    ]
    if c.subscriptions_not_collected:
        evidence.append("Never collected: " + ", ".join(c.subscriptions_not_collected) + ".")
    return _c("8.15", status, evidence)


def evaluate(
    numbers: dict[str, Any],
    by_rule: dict[str, list[ReportGrant]],
    decided: set[str],
    reviews: ReportReviews,
    changes: ReportChanges,
) -> list[ReportControl]:
    return [
        privileged(numbers),
        identities(numbers, by_rule.get("R01 orphan", []), decided),
        least_privilege(numbers),
        authentication(numbers),
        access_rights(reviews),
        logging_control(changes, reviews.subscriptions),
    ]
