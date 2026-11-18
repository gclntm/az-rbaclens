"""Azure Activity Log puller.

Phase 1 of the events feature. Pulls control-plane events (writes,
deletes, role-assignment changes, ARM operations) from
``MonitorManagementClient.activity_logs.list`` for a single
subscription over a time window, normalizes them into our
``activity_events`` shape, and persists with dedupe on
``(subscription_id, event_data_id)`` so re-pulls / overlapping
windows are idempotent.

Designed to run as a FastAPI BackgroundTask, mirroring the scan
flow: own session, sets status pending → running → done/failed,
emits progress so the UI can show liveness.

What this is NOT:
- A data-plane analyzer. Activity Log captures control-plane ARM
  calls only — reading a blob or decrypting a secret is invisible
  here. That's Phase 5 (requires diagnostic settings + LA).
- A time-series store. We persist raw events for ad-hoc analysis;
  trend / aggregate work happens at query time.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.mgmt.monitor import MonitorManagementClient

from app.core.activity_compaction import compacted_days, skip_on_compacted_day, utc_day
from app.core.activity_payload import slim_payload
from app.core.activity_targets import is_entra_audit, is_mgmt_groups
from app.core.azure import get_azure_transport, get_credential
from app.core.config import settings
from app.core.jobs import finish_job, mark_job_failed, start_job
from app.db import session as db_session
from app.db.models import ActivityEvent, ActivityPull

logger = logging.getLogger(__name__)

# Liveness while paging: a pull over a window it mostly has already
# (re-pulls, a retry after a failed pull) skips nearly every entry, so
# batches of new rows rarely fill. Beat on entries *read* as well, or
# the stuck-job sweeper fails a pull whose worker is still paging.
HEARTBEAT_EVERY_ENTRIES = 1000
HEARTBEAT_EVERY_SECONDS = 60.0
# How far before a pull's window stored events are looked at for duplicates:
# Azure filters on the same timestamp we store, so a day is generous.
DEDUPE_SLACK = timedelta(days=1)
_NIL_GUID = "00000000-0000-0000-0000-000000000000"


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------


def run_pull(pull_id: int) -> None:
    """Run a pull to completion. Marks done / failed and commits rows."""
    # Resolved at call time so test fixtures that swap
    # ``app.db.session.SessionLocal`` are honoured.
    with db_session.SessionLocal() as session:
        pull = session.get(ActivityPull, pull_id)
        if pull is None:
            logger.warning("activity pull %s not found, aborting", pull_id)
            return

        if not start_job(session, ActivityPull, pull_id):
            return
        pull.progress_done = 0
        pull.progress_message = "Starting…"
        pull.events_seen = 0
        pull.events_persisted = 0
        session.commit()

        try:
            credential = get_credential()
            transport = get_azure_transport()
            mgmt_groups = is_mgmt_groups(pull.subscription_id)
            entra = is_entra_audit(pull.subscription_id)
            not_readable: list[str] = []
            if entra:
                persisted, seen = _collect_entra_audit(session, credential, transport, pull)
            elif mgmt_groups:
                persisted, seen, not_readable = _collect_mgmt_groups(
                    session, credential, transport, pull
                )
            else:
                monitor = MonitorManagementClient(
                    credential, pull.subscription_id, transport=transport
                )
                persisted, seen = _collect_events(
                    session=session,
                    monitor=monitor,
                    pull=pull,
                )
            pull.events_seen = seen
            pull.events_persisted = persisted
            pull.heartbeat_at = datetime.now(UTC)
            pull.progress_message = (
                f"Done — {persisted} new membership changes ({seen - persisted} on file already)"
                if entra
                else f"Done — {persisted} new events ({seen - persisted} duplicates skipped)"
            )
            if not_readable:
                pull.progress_message += f" · Not readable: {', '.join(not_readable)}"
                logger.warning(
                    "activity pull %s: management groups not readable: %s",
                    pull_id,
                    ", ".join(not_readable),
                )
            # The sweeper may have marked the pull failed meanwhile: its
            # verdict stands (batches already committed stay, deduped).
            if not finish_job(session, ActivityPull, pull_id):
                return
            logger.info(
                "activity pull %s done — %d new / %d total for sub %s",
                pull_id,
                persisted,
                seen,
                pull.subscription_id,
            )
            # Usage-based findings depend on these events. Nothing new, nothing
            # to refresh. Otherwise the scheduler recomputes once for all the
            # pulls of this round (findings.flush_recompute); without a
            # scheduler loop (tests, one-off tools) it happens here.
            # Never fails the pull.
            if entra:
                # Not an Azure Activity Log: no usage findings, no operation
                # names. Who changed the groups the checks saw change.
                try:
                    from app.core.changes import refine_from_directory_audits

                    refine_from_directory_audits(session)
                except Exception:
                    logger.exception("changes refinement after pull %s failed", pull_id)
                    session.rollback()
                return
            if persisted:
                try:
                    from app.core import findings

                    if settings.scheduler_tick_seconds > 0:
                        findings.request_recompute(pull.subscription_id)
                    else:
                        recomputed = findings.recompute_for_subscriptions(
                            session, {pull.subscription_id}
                        )
                        if recomputed:
                            logger.info(
                                "activity pull %s: recomputed findings for scans %s",
                                pull_id,
                                recomputed,
                            )
                except Exception:
                    logger.exception("findings recompute after pull %s failed", pull_id)
                    session.rollback()
            # Names in words for the operations just seen. Never fails the pull.
            try:
                from app.core.operation_names import azure_fetcher, refresh_for_pull

                # Operation names are tenant-wide: any subscription id does.
                any_sub = _NIL_GUID if mgmt_groups else pull.subscription_id
                refresh_for_pull(session, pull, azure_fetcher(credential, any_sub, transport))
            except Exception:
                logger.exception("operation names refresh after pull %s failed", pull_id)
                session.rollback()
            # Exact time and author for changes the daily check saw first.
            try:
                from app.core.changes import refine_from_events

                refine_from_events(session, pull.subscription_id)
            except Exception:
                logger.exception("changes refinement after pull %s failed", pull_id)
                session.rollback()
        except Exception as exc:
            logger.exception("activity pull %s failed", pull_id)
            # Batches already committed by _collect_events stay (they are
            # deduped and valid); only the in-flight batch is discarded.
            mark_job_failed(session, ActivityPull, pull_id, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------------------


def _collect_events(
    *,
    session: Any,
    monitor: MonitorManagementClient,
    pull: ActivityPull,
) -> tuple[int, int]:
    """Iterate Activity Log entries in the window and persist new ones.

    Returns ``(persisted, seen)`` where ``persisted`` is the number of
    rows actually inserted (post-dedupe) and ``seen`` is the raw count
    Azure returned.

    Dedupe strategy: load the ``event_data_id`` already stored for this
    subscription *around the pull's window* (a day of slack before it),
    then skip any incoming id we already have. An hourly pull of a
    two-hour window loads a few thousand ids, not the whole retention.
    """
    # Activity Log REST takes a single OData ``$filter`` string. We
    # constrain by time + event channel. ``eventChannels eq
    # 'Admin, Operation'`` is what the portal uses by default and
    # filters out chatty service-health / autoscale entries we don't
    # care about for least-privilege analysis.
    fltr = (
        f"eventTimestamp ge '{_iso(pull.start_time)}' and "
        f"eventTimestamp le '{_iso(pull.end_time)}' and "
        "eventChannels eq 'Admin, Operation'"
    )

    return _persist_entries(session, pull, monitor.activity_logs.list(filter=fltr))


def _collect_entra_audit(
    session: Any, credential: Any, transport: Any, pull: ActivityPull
) -> tuple[int, int]:
    """Read the Entra ID audit log's membership changes for the window (tools/entra_audit.py).

    A refusal (permission or licence) is remembered, so Data & schedules
    says why, and fails the collection.
    """
    from app.tools import entra_audit

    try:
        found = entra_audit.collect(session, entra_audit.client(credential, transport), pull)
    except entra_audit.AuditLogNotReadable as exc:
        session.rollback()
        entra_audit.save_status(session, exc.reason, datetime.now(UTC))
        session.commit()
        raise
    if entra_audit.read_status(session).get("status") != entra_audit.READABLE:
        entra_audit.save_status(session, entra_audit.READABLE, datetime.now(UTC))
        session.commit()
    return found


def _collect_mgmt_groups(
    session: Any, credential: Any, transport: Any, pull: ActivityPull
) -> tuple[int, int, list[str]]:
    """Read every management group's log for the window: ``(persisted, seen, not readable)``.

    A group Azure refuses (not allowed, or deleted since the last check) is
    skipped and named; when every group is refused the collection fails.
    """
    from app.tools import mgmt_group_log

    groups = mgmt_group_log.groups_to_read(session)
    if not groups:
        raise RuntimeError(
            "No management group is known yet: it needs a first check of the tenant."
        )
    arm = mgmt_group_log.client(credential, transport)
    refused: list[str] = []

    def all_entries():
        for group, name in groups:
            try:
                yield from mgmt_group_log.entries(arm, group, pull.start_time, pull.end_time)
            except mgmt_group_log.GroupNotReadable:
                refused.append(name)

    persisted, seen = _persist_entries(session, pull, all_entries())
    if len(refused) == len(groups):
        raise PermissionError(
            "AuthorizationFailed: the Activity Log of no management group could be read "
            f"({len(groups)} tried); the identity needs Reader on the management groups."
        )
    return persisted, seen, refused


def _persist_entries(session: Any, pull: ActivityPull, entries: Any) -> tuple[int, int]:
    """Store the entries not on file yet, with progress and a heartbeat: ``(persisted, seen)``."""
    existing = {
        row[0]
        for row in session.execute(
            _select_existing_event_ids(pull.subscription_id, pull.start_time - DEDUPE_SLACK)
        ).all()
    }
    # Days whose machine activity is already folded into daily rows: those
    # events are counted, and their ids are gone (core/activity_compaction.py).
    folded = compacted_days(session, pull.subscription_id, pull.start_time - DEDUPE_SLACK)
    seen = 0
    persisted = 0
    batch: list[ActivityEvent] = []
    BATCH_SIZE = 200
    seen_at_beat = 0
    beat_at = time.monotonic()

    for entry in entries:
        seen += 1
        event_data_id = getattr(entry, "event_data_id", None)
        if event_data_id and event_data_id not in existing:
            existing.add(event_data_id)
            row = _normalize(entry, pull.subscription_id)
            if not (folded and utc_day(row.occurred_at) in folded and skip_on_compacted_day(row)):
                batch.append(row)
        if (
            len(batch) >= BATCH_SIZE
            or seen - seen_at_beat >= HEARTBEAT_EVERY_ENTRIES
            or time.monotonic() - beat_at >= HEARTBEAT_EVERY_SECONDS
        ):
            # One commit carries the new rows (possibly none) together
            # with the progress and heartbeat.
            session.add_all(batch)
            persisted += len(batch)
            batch = []
            pull.progress_done = persisted
            pull.progress_message = f"Read {seen} events, {persisted} new…"
            pull.heartbeat_at = datetime.now(UTC)
            session.commit()
            seen_at_beat = seen
            beat_at = time.monotonic()

    if batch:
        session.add_all(batch)
        session.commit()
        persisted += len(batch)
    pull.progress_done = persisted

    return persisted, seen


def _select_existing_event_ids(subscription_id: str, since: datetime):
    # Late import keeps the module importable without app.db.session
    # already bound (tests monkeypatch SessionLocal before importing
    # this module — but ``select`` itself is safe at module scope).
    from sqlalchemy import select

    return select(ActivityEvent.event_data_id).where(
        ActivityEvent.subscription_id == subscription_id,
        ActivityEvent.occurred_at >= since,
    )


# --------------------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Format datetime for the Activity Log OData filter.

    Azure wants ``YYYY-MM-DDTHH:MM:SS.mmmZ`` — the API rejects
    timezone offsets in the form ``+00:00`` even though they're
    technically ISO 8601. Force ``Z`` and drop the offset.
    """
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _normalize(entry: Any, subscription_id: str) -> ActivityEvent:
    """Convert one SDK ``EventData`` into our row shape."""
    op_name = _localized(getattr(entry, "operation_name", None))
    status = _localized(getattr(entry, "status", None))
    resource_id = getattr(entry, "resource_id", None)
    resource_type = _localized(getattr(entry, "resource_type", None))
    caller = getattr(entry, "caller", None)
    caller_type = _infer_caller_type(caller, getattr(entry, "claims", None))
    occurred_at = getattr(entry, "event_timestamp", None) or datetime.now(UTC)
    correlation_id = getattr(entry, "correlation_id", None)
    return ActivityEvent(
        subscription_id=subscription_id,
        event_data_id=getattr(entry, "event_data_id", ""),
        caller=caller,
        caller_type=caller_type,
        operation_name=op_name or "",
        resource_id=resource_id,
        resource_type=resource_type,
        status=status,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        # caller_oid is derived from the claims on insert (models.py).
        raw=slim_payload(_entry_to_dict(entry)),
    )


def _localized(field: Any) -> str | None:
    """Activity Log fields are ``LocalizableString(value, localized_value)``.

    Pick ``value`` (the canonical English / programmatic form) so
    downstream coverage analysis can intersect with role
    definition ``actions`` directly.
    """
    if field is None:
        return None
    value = getattr(field, "value", None)
    if isinstance(value, str) and value:
        return value
    localized = getattr(field, "localized_value", None)
    if isinstance(localized, str) and localized:
        return localized
    if isinstance(field, str):
        return field
    return None


def _infer_caller_type(caller: str | None, claims: dict[str, Any] | None) -> str | None:
    """Best-effort classification of who drove the operation.

    Azure doesn't tell us directly. Three signals, in order:

    1. JWT ``idtyp`` claim, when present: ``user`` / ``app``.
    2. ``http://schemas.microsoft.com/identity/claims/objectidentifier``
       + a UPN-looking caller → human user.
    3. Shape of ``caller``: UPN / email → user; bare GUID → SP or MI.

    Worth its own pass later (cross-reference with our
    role_assignments.principal_type) but the simple heuristic gets
    > 90 % of the way for our volumes.
    """
    if claims:
        idtyp = claims.get("idtyp")
        if isinstance(idtyp, str):
            if idtyp == "user":
                return "User"
            if idtyp == "app":
                return "ServicePrincipal"
    if not caller:
        return None
    if "@" in caller:
        return "User"
    # GUID-ish — 8-4-4-4-12 hex
    if len(caller) == 36 and caller.count("-") == 4:
        return "ServicePrincipal"
    return None


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    """Best-effort JSON-able snapshot. Falls back to ``str()`` on the
    SDK model when ``as_dict`` isn't available."""
    if hasattr(entry, "as_dict"):
        try:
            return entry.as_dict()
        except Exception:  # SDK serialisation is best effort
            logger.debug("as_dict failed on %s", type(entry).__name__, exc_info=True)
    return {"repr": str(entry)}
