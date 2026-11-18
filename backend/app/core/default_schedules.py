"""Default schedules: the daily check of the whole tenant, and an hourly
Activity Log pull for every subscription and for the management groups.
The hourly collection of the Entra ID audit log is added only once the
identity can read it (``tools/entra_audit.py``: tried after each check).

Created once, when the registry first knows the tenant's root management
group (after the first check), and kept up to date afterwards: a
subscription found later gets its pull schedule after the check that
found it. A schedule an admin deletes is remembered in ``app_settings``
and never re-created. ``settings.default_schedules = False`` turns all of
this off (tests, throwaway stacks).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.activity_targets import ENTRA_AUDIT, MGMT_GROUPS
from app.core.config import settings
from app.core.scheduler import next_run, normalize_target
from app.db.models import AppSetting, JobSchedule, Scope

logger = logging.getLogger(__name__)

STATE_KEY = "default_schedules"


def _state(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, STATE_KEY)
    return dict(row.value) if row else {}


def _save(session: Session, state: dict[str, Any]) -> None:
    row = session.get(AppSetting, STATE_KEY)
    if row is None:
        session.add(AppSetting(key=STATE_KEY, value=state))
    else:
        row.value = state


def decline(session: Session, kind: str, target: str) -> None:
    """Remember that an admin removed this schedule, so it is not re-created.

    The caller commits.
    """
    state = _state(session)
    declined = set(state.get("declined", []))
    declined.add(f"{kind}:{target}")
    state["declined"] = sorted(declined)
    _save(session, state)


def ensure_default_schedules(session: Session, now: datetime) -> list[JobSchedule]:
    """Create the default schedules that are missing; returns the new ones. Commits."""
    if not settings.default_schedules:
        return []
    state = _state(session)
    declined = set(state.get("declined", []))
    existing = {
        (k, t) for k, t in session.execute(select(JobSchedule.kind, JobSchedule.target)).all()
    }
    created: list[JobSchedule] = []

    def add(kind: str, target: str, **fields: Any) -> None:
        if (kind, target) in existing or f"{kind}:{target}" in declined:
            return
        schedule = JobSchedule(kind=kind, target=target, **fields)
        session.add(schedule)
        existing.add((kind, target))
        created.append(schedule)

    if not state.get("check_created"):
        roots = session.scalars(
            select(Scope.id).where(Scope.level == "mgmt_group", Scope.parent_id.is_(None))
        ).all()
        if not roots:
            return []  # nothing checked yet; try again after the first check
        hour = settings.default_check_hour
        for root in roots:
            add(
                "snapshot",
                normalize_target("snapshot", root),
                every_hours=24,
                at_hour=hour,
                next_run_at=next_run(24, hour, now),
            )
        state["check_created"] = now.isoformat()

    for sub in session.scalars(select(Scope.id).where(Scope.level == "subscription")).all():
        add(
            "activity_pull",
            normalize_target("activity_pull", sub.rsplit("/", 1)[-1]),
            every_hours=1,
            initial_window_days=90,
            next_run_at=now,
        )
    # One collection for the logs of all management groups (core/activity_targets.py).
    if session.scalar(select(Scope.id).where(Scope.level == "mgmt_group").limit(1)) is not None:
        add("activity_pull", MGMT_GROUPS, every_hours=1, initial_window_days=90, next_run_at=now)
    # Entra keeps its audit log 30 days at most: the first collection reads all of it.
    from app.tools.entra_audit import READABLE, read_status

    if read_status(session).get("status") == READABLE:
        add("activity_pull", ENTRA_AUDIT, every_hours=1, initial_window_days=30, next_run_at=now)

    _save(session, state)
    session.commit()
    if created:
        logger.info(
            "default schedules created: %s", ", ".join(f"{s.kind} {s.target}" for s in created)
        )
    return created
