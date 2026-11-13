"""ARM operations in words: fallback, cache lookup, provider refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import operation_names as on
from app.core.operation_names import FetchedOperation, fallback_name, names_for, refresh_providers

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_failures():
    on._failed.clear()
    yield
    on._failed.clear()


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("Microsoft.Compute/virtualMachines/write", "Create or update virtual machine"),
        ("Microsoft.Compute/disks/delete", "Delete disk"),
        (
            "microsoft.storage/storageaccounts/listAccountSas/action",
            "List account sas (storageaccount)",
        ),
        ("Microsoft.Storage/storageAccounts/listKeys/action", "List keys (storage account)"),
        ("Microsoft.Network/publicIPAddresses/read", "Read public ip address"),
        ("Microsoft.Resources/deployments/write", "Create or update deployment"),
        ("Microsoft.Authorization/policies/audit/action", "Audit (policy)"),
        ("Microsoft.Insights/register/action", "Register (insights)"),
        ("weird", "weird"),
    ],
)
def test_fallback_name(operation, expected):
    assert fallback_name(operation) == expected


def _session(client: TestClient):
    from app.db.session import SessionLocal

    return SessionLocal()


def _fetcher(calls: list[str], ops: dict[str, list[FetchedOperation]]):
    def fetch(provider: str):
        calls.append(provider)
        if provider not in ops:
            raise RuntimeError("no metadata")
        return ops[provider]

    return fetch


COMPUTE = [
    FetchedOperation(
        "Microsoft.Compute/virtualMachines/write", "Create or Update Virtual Machine", None
    ),
    FetchedOperation(
        "Microsoft.Compute/virtualMachines/write",
        "Create or Update Virtual Machine",
        "Virtual Machines",
    ),
]


def test_refresh_caches_names_case_insensitively(app_with_sqlite: TestClient):
    calls: list[str] = []
    with _session(app_with_sqlite) as db:
        assert (
            refresh_providers(
                db, ["Microsoft.Compute"], _fetcher(calls, {"microsoft.compute": COMPUTE}), NOW
            )
            == 1
        )
        got = names_for(
            db, ["MICROSOFT.COMPUTE/virtualMachines/WRITE", "Microsoft.Compute/disks/delete"]
        )
    assert calls == ["microsoft.compute"]
    assert got == {
        "MICROSOFT.COMPUTE/virtualMachines/WRITE": "Create or Update Virtual Machine",
        "Microsoft.Compute/disks/delete": "Delete disk",  # not in metadata → fallback
    }


def test_refresh_skips_fresh_providers_and_refetches_stale(app_with_sqlite: TestClient):
    calls: list[str] = []
    fetch = _fetcher(calls, {"microsoft.compute": COMPUTE})
    with _session(app_with_sqlite) as db:
        refresh_providers(db, ["microsoft.compute"], fetch, NOW)
        assert refresh_providers(db, ["microsoft.compute"], fetch, NOW + timedelta(days=6)) == 0
        assert refresh_providers(db, ["microsoft.compute"], fetch, NOW + timedelta(days=8)) == 1
    assert calls == ["microsoft.compute", "microsoft.compute"]


def test_failed_provider_waits_a_day_and_does_not_block_others(app_with_sqlite: TestClient):
    calls: list[str] = []
    fetch = _fetcher(calls, {"microsoft.compute": COMPUTE})
    with _session(app_with_sqlite) as db:
        assert (
            refresh_providers(db, ["microsoft.azurearcdata", "microsoft.compute"], fetch, NOW) == 1
        )
        refresh_providers(db, ["microsoft.azurearcdata"], fetch, NOW + timedelta(hours=2))
        refresh_providers(db, ["microsoft.azurearcdata"], fetch, NOW + timedelta(days=2))
    assert calls == ["microsoft.azurearcdata", "microsoft.compute", "microsoft.azurearcdata"]


def test_events_endpoint_returns_operation_display(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent

    with _session(app_with_sqlite) as db:
        refresh_providers(
            db, ["microsoft.compute"], _fetcher([], {"microsoft.compute": COMPUTE}), NOW
        )
        db.add(
            ActivityEvent(
                subscription_id="sub-1",
                event_data_id="e1",
                operation_name="Microsoft.Compute/virtualMachines/write",
                occurred_at=NOW,
                raw={},
            )
        )
        db.add(
            ActivityEvent(
                subscription_id="sub-1",
                event_data_id="e2",
                operation_name="Microsoft.Storage/storageAccounts/listKeys/action",
                occurred_at=NOW,
                raw={},
            )
        )
        db.commit()
    r = app_with_sqlite.get("/api/activity/events", params={"subscription_id": "sub-1"})
    assert r.status_code == 200, r.text
    got = {e["operation_name"]: e["operation_display"] for e in r.json()}
    assert got == {
        "Microsoft.Compute/virtualMachines/write": "Create or Update Virtual Machine",
        "Microsoft.Storage/storageAccounts/listKeys/action": "List keys (storage account)",
    }
