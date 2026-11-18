"""What RBAC Lens keeps, and for how long: saved values over the server's defaults.

Settings › Data kept saves values in ``app_settings`` (key ``retention``).
Each one is optional: a value never saved, or cleared, falls back to the
server's environment variable (``app/core/config.py``), so a fresh install
from the images, or a stack configured by environment, works as before.
Every reader of these settings goes through :func:`effective`.

``0`` days keeps everything, as for the environment variables; for
``compact_after_days`` it means "never fold app activity".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AppSetting

KEY = "retention"
# (lowest, highest) accepted for each value.
BOUNDS: dict[str, tuple[int, int]] = {
    "snapshot_keep_days": (0, 3650),
    "snapshot_keep_per_scope": (1, 1000),
    "failed_snapshot_retention_days": (0, 3650),
    "event_retention_days": (0, 3650),
    "entra_audit_retention_days": (0, 3650),
    # 0 = never; else at least 3 days: collections re-read the last two days
    # (the nightly second pass), so a younger day may still change.
    "compact_after_days": (3, 3650),
    "change_retention_days": (0, 3650),
    "audit_retention_days": (0, 3650),
    "pull_record_retention_days": (0, 3650),
}
# Values that also accept 0 below their lowest bound ("never").
ZERO_MEANS_NEVER = {"compact_after_days"}


def _valid(name: str, value: int) -> bool:
    low, high = BOUNDS[name]
    return low <= value <= high or (value == 0 and name in ZERO_MEANS_NEVER)


@dataclass(frozen=True)
class RetentionSettings:
    # Checks: kept while younger than ``snapshot_keep_days`` (0 = no time rule)
    # or among the newest ``snapshot_keep_per_scope`` of their place.
    snapshot_keep_days: int
    snapshot_keep_per_scope: int
    failed_snapshot_retention_days: int
    # The Activity Log and the Entra ID audit log, each its own.
    event_retention_days: int
    entra_audit_retention_days: int
    # Routine app activity is folded into daily counts after this many days (0 = never).
    compact_after_days: int
    change_retention_days: int
    audit_retention_days: int
    pull_record_retention_days: int


NAMES = tuple(f.name for f in fields(RetentionSettings))


def defaults() -> RetentionSettings:
    """The server's values (environment variables, else built-in defaults)."""
    entra = settings.entra_audit_retention_days
    return RetentionSettings(
        snapshot_keep_days=settings.snapshot_keep_days,
        snapshot_keep_per_scope=settings.snapshot_keep_per_scope,
        failed_snapshot_retention_days=settings.failed_snapshot_retention_days,
        event_retention_days=settings.event_retention_days,
        # Unset: the Entra ID audit log is kept as long as the Activity Log.
        entra_audit_retention_days=settings.event_retention_days if entra is None else entra,
        compact_after_days=settings.compact_after_days,
        change_retention_days=settings.change_retention_days,
        audit_retention_days=settings.audit_retention_days,
        pull_record_retention_days=settings.pull_record_retention_days,
    )


def saved(session: Session) -> dict[str, int]:
    """The values an admin saved (valid ones only; anything else is ignored)."""
    row = session.get(AppSetting, KEY)
    raw = row.value if row and isinstance(row.value, dict) else {}
    out: dict[str, int] = {}
    for name, value in raw.items():
        if (
            name in BOUNDS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and _valid(name, value)
        ):
            out[name] = value
    return out


def effective(session: Session) -> RetentionSettings:
    """What retention applies now: each saved value, else the server's."""
    return RetentionSettings(**{**asdict(defaults()), **saved(session)})


def sources(session: Session) -> dict[str, str]:
    """``saved`` or ``server`` for each value."""
    mine = saved(session)
    return {name: "saved" if name in mine else "server" for name in NAMES}


class InvalidRetention(ValueError):
    """A value out of bounds or unknown; the message says which."""


def _merged(session: Session, values: dict[str, Any]) -> dict[str, int]:
    """The saved values with ``values`` applied (``None`` clears one). Raises
    :class:`InvalidRetention`."""
    current = saved(session)
    for name, value in values.items():
        if name not in BOUNDS:
            raise InvalidRetention(f"unknown setting: {name}")
        if value is None:
            current.pop(name, None)
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidRetention(f"{name} must be a whole number")
        if not _valid(name, value):
            low, high = BOUNDS[name]
            never = " (or 0, never)" if name in ZERO_MEANS_NEVER else ""
            raise InvalidRetention(f"{name} must be between {low} and {high}{never}")
        current[name] = value
    return current


def proposed(session: Session, values: dict[str, Any]) -> RetentionSettings:
    """What would apply if ``values`` were saved (for the preview). Raises
    :class:`InvalidRetention`."""
    return RetentionSettings(**{**asdict(defaults()), **_merged(session, values)})


def save(session: Session, values: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """Store ``values`` (``None`` clears one: back to the server's). The caller
    commits. Returns the effective values before and after, for the audit log.
    Raises :class:`InvalidRetention`."""
    before = asdict(effective(session))
    current = _merged(session, values)
    row = session.get(AppSetting, KEY)
    if row is None:
        session.add(AppSetting(key=KEY, value=current))
    else:
        row.value = current
    session.flush()
    return before, asdict(effective(session))
