"""Is the data still being kept up to date? Warnings for the Home page and the admins.

The picture only stays true while the schedules run: the daily check of the
tenant, and the hourly Activity Log pull per subscription. When one is off,
fails, or falls behind, nothing else in the app says so. :func:`warnings`
lists what is wrong, worst first:

- ``schedules_off``: a schedule is switched off (deleting it instead says
  "on purpose", and stops the warning);
- ``no_check``: no check is scheduled at all;
- ``check_failed``: the last scheduled check failed;
- ``check_stale``: the newest check is more than :data:`CHECK_STALE_HOURS` old;
- ``activity_behind``: a subscription's Activity Log, though scheduled, is
  more than two pulls behind (a pull still running doesn't count);
- ``activity_at_risk`` (red): a subscription, paused or not, is so far
  behind that under 30 days are left before Azure deletes the oldest
  uncollected events (it keeps 90);
- ``activity_lost`` (red): more than 90 days behind, so some are gone.

:func:`notify_admins` turns them into one email to the admins per day and
set of problems (``data_stale``), called from the scheduler tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.activity_targets import is_entra_audit, is_mgmt_groups
from app.core.coverage import AZURE_KEEPS_DAYS, coverage
from app.core.job_errors import explain
from app.core.timeutil import as_utc
from app.db.models import JobSchedule, Scan, Scope

# A daily check plus two hours of slack (the side menu uses the same limit).
CHECK_STALE_HOURS = 26
DATA_PATH = "/admin/data"


@dataclass(frozen=True)
class StaleWarning:
    kind: str
    text: str
    names: list[str] = field(default_factory=list)
    path: str = DATA_PATH
    # "danger" when data is being lost or about to be; "warn" otherwise.
    severity: str = "warn"


def _names(session: Session) -> dict[str, str]:
    return {
        sid.lower(): name
        for sid, name in session.execute(select(Scope.id, Scope.name)).all()
        if name
    }


def _target_name(schedule: JobSchedule, names: dict[str, str]) -> str:
    target = schedule.target.lower()
    if schedule.kind == "activity_pull" and is_mgmt_groups(target):
        return "the management groups"
    if schedule.kind == "activity_pull" and is_entra_audit(target):
        return "the Entra ID audit log"
    key = target if schedule.kind == "snapshot" else f"/subscriptions/{target}"
    return names.get(key) or target.rsplit("/", 1)[-1]


def _list(items: list[str], limit: int = 4) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many.format(n=n)


def _hours(h: float) -> str:
    """ "5 hours", or "3 days" from two days on."""
    return f"{int(h)} hours" if h < 48 else f"{int(h // 24)} days"


def _schedules_off(off: list[JobSchedule], names: dict[str, str]) -> StaleWarning:
    checks = [_target_name(s, names) for s in off if s.kind == "snapshot"]
    pulls = [_target_name(s, names) for s in off if s.kind == "activity_pull"]
    parts = []
    if checks:
        parts.append(f"the check of {_list(checks)}")
    if pulls:
        parts.append(
            _plural(
                len(pulls),
                f"the Activity Log of {pulls[0]}",
                "the Activity Log of {n} subscriptions",
            )
        )
    lead = _plural(len(off), "A schedule is off", "{n} schedules are off")
    tail = _plural(
        len(off),
        "Nothing updates it until it's on again; delete it if you don't want it.",
        "Nothing updates them until they're on again; delete the ones you don't want.",
    )
    return StaleWarning(
        "schedules_off",
        f"{lead}: {' and '.join(parts)}. {tail}",
        checks + pulls,
    )


def _check_warnings(
    session: Session, snapshots: list[JobSchedule], now: datetime
) -> list[StaleWarning]:
    enabled = [s for s in snapshots if s.enabled]
    if not snapshots:
        return [
            StaleWarning(
                "no_check", "No check is scheduled, so who can do what is never refreshed."
            )
        ]
    out: list[StaleWarning] = []
    daily = next((s for s in enabled if s.at_hour is not None), enabled[0] if enabled else None)
    if daily and daily.last_job_id:
        last = session.get(Scan, daily.last_job_id)
        if last is not None and last.status == "failed":
            reason = explain(last.error) or "No reason was recorded."
            out.append(StaleWarning("check_failed", f"The last scheduled check failed. {reason}"))
    targets = [s.target.lower() for s in snapshots]
    newest = as_utc(
        session.scalar(
            select(func.max(Scan.finished_at)).where(
                Scan.status == "done", func.lower(Scan.scope).in_(targets)
            )
        )
    )
    if newest is None or now - newest > timedelta(hours=CHECK_STALE_HOURS):
        age = (
            "has never run"
            if newest is None
            else f"last ran {_hours((now - newest).total_seconds() / 3600)} ago"
        )
        out.append(
            StaleWarning(
                "check_stale", f"The scheduled check {age}: who can do what may be out of date."
            )
        )
    return out


def _activity_warnings(session: Session, now: datetime) -> list[StaleWarning]:
    """Activity Log collection that is behind, and, in red, what Azure is about to forget.

    ``activity_lost`` / ``activity_at_risk`` cover every schedule, paused
    ones included: Azure's 90 days run whether collection is on or not.
    ``activity_behind`` is for schedules that are on but lagging (a paused
    one is already named by ``schedules_off``; one collecting now is catching up).
    """
    rows = [c for c in coverage(session, now) if c.schedule_id is not None]
    out: list[StaleWarning] = []

    lost = [c for c in rows if c.state == "lost"]
    if lost:
        days = max(-(c.days_left or 0) for c in lost)
        who = _plural(
            len(lost),
            f"The Activity Log of {lost[0].name}",
            "The Activity Log of {n} subscriptions",
        )
        out.append(
            StaleWarning(
                "activity_lost",
                f"{who} was not collected for more than {AZURE_KEEPS_DAYS} days: up to {max(days, 1)} "
                f"{'day' if max(days, 1) == 1 else 'days'} of changes are gone from Azure for good. "
                "Resume collection to stop losing more.",
                [c.name for c in lost],
                severity="danger",
            )
        )

    at_risk = sorted((c for c in rows if c.state == "at_risk"), key=lambda c: c.days_left or 0)
    if at_risk:
        left = at_risk[0].days_left or 0
        who = _plural(
            len(at_risk),
            f"The Activity Log of {at_risk[0].name}",
            "The Activity Log of {n} subscriptions",
        )
        out.append(
            StaleWarning(
                "activity_at_risk",
                f"{who} is {_hours(at_risk[0].behind_hours or 0)} behind. Azure deletes what is not collected "
                f"within {AZURE_KEEPS_DAYS} days: {left} {'day' if left == 1 else 'days'} left to resume it.",
                [c.name for c in at_risk],
                severity="danger",
            )
        )

    behind = [c for c in rows if c.enabled and not c.collecting and c.state in ("behind", "never")]
    if behind:
        behind.sort(key=lambda c: -(c.behind_hours if c.behind_hours is not None else float("inf")))
        worst = behind[0]
        if worst.behind_hours is None:
            how = "has never been collected"
        else:
            how = f"is up to {_hours(worst.behind_hours)} behind"
            if worst.behind_hours >= 24 and worst.days_left is not None:
                how += f" ({worst.days_left} days left before Azure deletes the oldest)"
        who = _plural(
            len(behind),
            f"The Activity Log of {worst.name}",
            "The Activity Log of {n} subscriptions",
        )
        out.append(
            StaleWarning(
                "activity_behind",
                f"{who} {how}: recent changes and who made them are missing.",
                [c.name for c in behind],
            )
        )
    return out


def warnings(session: Session, now: datetime | None = None) -> list[StaleWarning]:
    """What keeps the data from being up to date, worst first. Empty = all fine."""
    now = now or datetime.now(UTC)
    schedules = list(session.scalars(select(JobSchedule).order_by(JobSchedule.id)).all())
    names = _names(session)
    out: list[StaleWarning] = []
    off = [s for s in schedules if not s.enabled]
    if off:
        out.append(_schedules_off(off, names))
    out.extend(_check_warnings(session, [s for s in schedules if s.kind == "snapshot"], now))
    out.extend(_activity_warnings(session, now))
    # Red first: what is being lost comes before what is merely late.
    out.sort(key=lambda w: 0 if w.severity == "danger" else 1)
    return out


def notify_admins(session: Session, now: datetime | None = None) -> int:
    """Queue one ``data_stale`` email to the admins per day and set of problems. Commits.

    Returns how many were queued (0 or 1).
    """
    from app.core.notify import enqueue

    now = now or datetime.now(UTC)
    found = warnings(session, now)
    if not found:
        return 0
    kinds = "+".join(sorted({w.kind for w in found}))
    row = enqueue(
        session,
        "data_stale",
        recipient_id=None,
        recipient_name=None,
        subject_ref="data",
        dedupe_key=f"data_stale:{now.date().isoformat()}:{kinds}",
        payload={"count": len(found), "lines": [w.text for w in found], "path": DATA_PATH},
    )
    session.commit()
    return 1 if row is not None else 0
