"""ARM operations in words.

The Activity Log names operations like
``Microsoft.Compute/virtualMachines/write``. Azure publishes a display
name for each one in its provider operations metadata ("Create or Update
Virtual Machine"). This module caches those names in ``operation_names``,
refreshes them for the providers an Activity Log pull just saw (at most
weekly per provider), and falls back to a name built from the operation
itself when Azure has none.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import ActivityEvent, ActivityPull, OperationName

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(days=7)
# A provider whose metadata could not be fetched is not retried for this long.
FAILED_RETRY = timedelta(days=1)

_VERBS = {
    "read": "Read",
    "write": "Create or update",
    "delete": "Delete",
}


@dataclass(frozen=True)
class FetchedOperation:
    operation: str
    display_name: str
    resource_type_display: str | None


# ``(provider namespace) -> operations``; raises on failure.
Fetcher = Callable[[str], list[FetchedOperation]]

_failed: dict[str, datetime] = {}


def provider_of(operation: str) -> str:
    return operation.split("/", 1)[0].lower()


def fallback_name(operation: str) -> str:
    """A readable name built from the operation string alone.

    ``Microsoft.Compute/virtualMachines/write`` → "Create or update virtual machine";
    ``Microsoft.Storage/storageAccounts/listKeys/action`` → "List keys (storage account)".
    """
    parts = [p for p in operation.split("/") if p]
    if len(parts) < 2:
        return operation
    verb = parts[-1].lower()
    if verb == "action" and len(parts) >= 3:
        resource = _resource(parts[1:-2]) or _words(parts[0].split(".")[-1])
        action = _words(parts[-2])
        return f"{action[:1].upper()}{action[1:]} ({resource})" if resource else action.capitalize()
    resource = _resource(parts[1:-1]) or _words(parts[0].split(".")[-1])
    if verb in _VERBS:
        return f"{_VERBS[verb]} {resource}"
    return f"{_words(parts[-1]).capitalize()} {resource}"


def names_for(session: Session, operations: Iterable[str]) -> dict[str, str]:
    """``{operation as given: display name}``, from the cache or the fallback."""
    ops = {o for o in operations if o}
    if not ops:
        return {}
    keys = {o.lower() for o in ops}
    cached: dict[str, str] = {}
    for chunk in _chunks(sorted(keys)):
        cached.update(
            session.execute(
                select(OperationName.operation, OperationName.display_name).where(
                    OperationName.operation.in_(chunk)
                )
            ).all()
        )
    return {o: cached.get(o.lower()) or fallback_name(o) for o in ops}


def refresh_providers(
    session: Session,
    providers: Iterable[str],
    fetch: Fetcher,
    now: datetime | None = None,
) -> int:
    """Re-fetch providers that are missing or older than ``MAX_AGE``; returns how many were refreshed."""
    now = now or datetime.now(UTC)
    wanted = {p.lower() for p in providers if p}
    fresh = {
        p
        for p, fetched in session.execute(
            select(OperationName.provider, OperationName.fetched_at).where(
                OperationName.provider.in_(wanted or {""})
            )
        ).all()
        if as_utc(fetched) > now - MAX_AGE
    }
    refreshed = 0
    for provider in sorted(wanted - fresh):
        failed_at = _failed.get(provider)
        if failed_at and failed_at > now - FAILED_RETRY:
            continue
        try:
            fetched_ops = fetch(provider)
        except Exception as exc:  # noqa: BLE001 - one provider must not stop the others
            logger.info("operation names for %s not fetched: %s", provider, exc)
            _failed[provider] = now
            continue
        _failed.pop(provider, None)
        rows = {}
        for op in fetched_ops:
            if op.operation and op.display_name:
                rows[op.operation.lower()] = OperationName(
                    operation=op.operation.lower(),
                    provider=provider,
                    display_name=op.display_name,
                    resource_type_display=op.resource_type_display,
                    fetched_at=now,
                )
        if not rows:
            _failed[provider] = now
            continue
        session.execute(delete(OperationName).where(OperationName.provider == provider))
        session.add_all(rows.values())
        session.commit()
        refreshed += 1
    return refreshed


def refresh_for_pull(session: Session, pull: ActivityPull, fetch: Fetcher) -> int:
    """Refresh the providers seen in a finished pull's window."""
    stmt = select(ActivityEvent.operation_name).where(
        ActivityEvent.subscription_id == pull.subscription_id
    )
    if pull.start_time is not None:
        stmt = stmt.where(ActivityEvent.occurred_at >= pull.start_time)
    providers = {provider_of(op) for op in session.scalars(stmt.distinct()).all() if op}
    return refresh_providers(session, providers, fetch)


def azure_fetcher(credential: Any, subscription_id: str, transport: Any = None) -> Fetcher:
    """A ``Fetcher`` backed by ``providerOperations`` (tenant-wide; any subscription id works)."""
    from azure.mgmt.authorization import AuthorizationManagementClient

    kwargs = {"transport": transport} if transport is not None else {}
    client = AuthorizationManagementClient(credential, subscription_id, **kwargs)

    def fetch(provider: str) -> list[FetchedOperation]:
        meta = client.provider_operations_metadata.get(provider, expand="resourceTypes")
        out = [
            FetchedOperation(o.name, o.display_name, None)
            for o in meta.operations or []
            if o.name and o.display_name
        ]
        for rt in meta.resource_types or []:
            out.extend(
                FetchedOperation(o.name, o.display_name, rt.display_name)
                for o in rt.operations or []
                if o.name and o.display_name
            )
        return out

    return fetch


def _resource(segments: list[str]) -> str:
    """Last resource type segment, in words and singular: ``virtualMachines`` → "virtual machine"."""
    if not segments:
        return ""
    return _singular(_words(segments[-1]))


def _words(token: str) -> str:
    # ``publicIPAddresses`` → "public IP Addresses"
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", token)
    return spaced.replace("_", " ").replace("-", " ").lower().strip()


def _singular(words: str) -> str:
    head, _, last = words.rpartition(" ")
    if last.endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.endswith(("sses", "xes", "ches", "shes")):
        last = last[:-2]
    elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        last = last[:-1]
    return f"{head} {last}" if head else last


def _chunks(items: list[str], size: int = 500) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
