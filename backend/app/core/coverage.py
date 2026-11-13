"""Activity Log coverage: is every subscription collected, and how long before Azure forgets?

Azure keeps the Activity Log for 90 days. A subscription whose collection
is paused or failing loses nothing as long as it resumes within that time;
after it, the oldest events are gone for good. :func:`coverage` says, per
subscription, up to when it is collected (``job_schedules.covered_until``),
how far behind that is, and how many days are left before the first
uncollected event leaves Azure's 90 days.

Besides the subscriptions there is one row for the logs of all management
groups (``core/activity_targets.py``), collected together.

It also reports the **second pass** (``core/scheduler.start_second_pass``):
once a night each subscription's last two days are read again, so an event
Azure delivered more than an hour late, after its window was collected, is
still caught. What the pass inserts is counted as "late events".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.activity_targets import MGMT_GROUPS, MGMT_GROUPS_NAME
from app.core.timeutil import as_utc
from app.db.models import ActivityPull, JobSchedule, Scope

# What Azure keeps, and the day of safety the scheduler leaves (scheduler.RETENTION_MARGIN).
AZURE_KEEPS_DAYS = 90
COLLECTABLE_DAYS = AZURE_KEEPS_DAYS - 1
# Under this many days left, the warning turns red and names the deadline.
AT_RISK_DAYS_LEFT = 30
# Hours an hourly collection may lag before it counts as behind: its interval plus this.
SLACK_HOURS = 2
SECOND_PASS_ORIGIN = "recheck"  # noqa: S105 - a job origin, not a secret
_IN_FLIGHT = ("pending", "running")

State = Literal["ok", "behind", "at_risk", "lost", "never", "no_schedule"]


@dataclass(frozen=True)
class SecondPass:
    """The newest second pass of a subscription."""

    status: str
    finished_at: datetime | None
    late_events: int


@dataclass(frozen=True)
class SubscriptionCoverage:
    subscription_id: str
    name: str
    schedule_id: int | None
    enabled: bool
    # A collection of this subscription is running now.
    collecting: bool
    covered_until: datetime | None
    behind_hours: float | None
    # Days before the first uncollected event leaves Azure's 90 days; negative = already lost.
    days_left: int | None
    state: State
    second_pass: SecondPass | None


def _state(behind_hours: float | None, days_left: int | None, every_hours: int) -> State:
    if behind_hours is None or days_left is None:
        return "never"
    if days_left < 0:
        return "lost"
    if behind_hours <= every_hours + SLACK_HOURS:
        return "ok"
    return "at_risk" if days_left < AT_RISK_DAYS_LEFT else "behind"


def coverage(session: Session, now: datetime | None = None) -> list[SubscriptionCoverage]:
    """Every known subscription, worst state first, then by name."""
    now = now or datetime.now(UTC)
    schedules = {
        s.target.lower(): s
        for s in session.scalars(
            select(JobSchedule).where(JobSchedule.kind == "activity_pull")
        ).all()
    }
    names = {
        sid.lower().rsplit("/", 1)[-1]: name
        for sid, name in session.execute(
            select(Scope.id, Scope.name).where(Scope.level == "subscription")
        ).all()
    }
    busy = set(
        session.scalars(
            select(func.lower(ActivityPull.subscription_id)).where(
                ActivityPull.status.in_(_IN_FLIGHT)
            )
        ).all()
    )
    passes = _second_passes(session)
    # The management groups' logs are one collection (core/activity_targets.py):
    # one row, as soon as a check has found a management group.
    if (
        MGMT_GROUPS in schedules
        or session.scalar(select(Scope.id).where(Scope.level == "mgmt_group").limit(1)) is not None
    ):
        names[MGMT_GROUPS] = MGMT_GROUPS_NAME

    out: list[SubscriptionCoverage] = []
    for sub in sorted(set(schedules) | set(names)):
        s = schedules.get(sub)
        until = as_utc(s.covered_until) if s else None
        behind = None if until is None else max(0.0, (now - until).total_seconds() / 3600)
        days_left = None if behind is None else int(COLLECTABLE_DAYS - behind / 24)
        state: State = "no_schedule" if s is None else _state(behind, days_left, s.every_hours)
        out.append(
            SubscriptionCoverage(
                subscription_id=sub,
                name=names.get(sub) or sub,
                schedule_id=s.id if s else None,
                enabled=bool(s and s.enabled),
                collecting=sub in busy,
                covered_until=until,
                behind_hours=None if behind is None else round(behind, 1),
                days_left=days_left,
                state=state,
                second_pass=passes.get(sub),
            )
        )
    rank = {"lost": 0, "at_risk": 1, "never": 2, "no_schedule": 3, "behind": 4, "ok": 5}
    out.sort(key=lambda c: (rank[c.state], c.name.lower()))
    return out


def _second_passes(session: Session) -> dict[str, SecondPass]:
    newest = (
        select(func.max(ActivityPull.id))
        .where(ActivityPull.origin == SECOND_PASS_ORIGIN)
        .group_by(func.lower(ActivityPull.subscription_id))
    )
    return {
        sub: SecondPass(status=status, finished_at=as_utc(finished), late_events=late or 0)
        for sub, status, finished, late in session.execute(
            select(
                func.lower(ActivityPull.subscription_id),
                ActivityPull.status,
                ActivityPull.finished_at,
                ActivityPull.events_persisted,
            ).where(ActivityPull.id.in_(newest))
        ).all()
    }


@dataclass(frozen=True)
class SecondPassSummary:
    # When the newest finished pass ended; ``None`` before the first one.
    last_finished_at: datetime | None
    # Subscriptions whose newest pass finished, and how many it covers.
    subscriptions: int
    late_events: int
    failed: int


def second_pass_summary(rows: list[SubscriptionCoverage]) -> SecondPassSummary:
    done = [r.second_pass for r in rows if r.second_pass and r.second_pass.status == "done"]
    finished = [p.finished_at for p in done if p.finished_at]
    return SecondPassSummary(
        last_finished_at=max(finished) if finished else None,
        subscriptions=len(done),
        late_events=sum(p.late_events for p in done),
        failed=sum(1 for r in rows if r.second_pass and r.second_pass.status == "failed"),
    )
