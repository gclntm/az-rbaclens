"""Slimmed Activity Log payloads and the derived ``caller_oid`` column."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.activity_payload import (
    OID_CLAIM,
    UPN_CLAIM,
    caller_oid_from_payload,
    slim_payload,
)

FULL = {
    "id": "/subscriptions/s/providers/microsoft.insights/eventtypes/management/values/e1",
    "event_data_id": "e1",
    "subscription_id": "s",
    "caller": "alice@example.com",
    "operation_name": {"value": "Microsoft.Storage/storageAccounts/write"},
    "status": {"value": "Succeeded"},
    "resource_id": "/subscriptions/s/resourceGroups/rg",
    "resource_type": {"value": "Microsoft.Storage/storageAccounts"},
    "correlation_id": "c1",
    "event_timestamp": "2026-10-22T00:00:00Z",
    "description": "",
    "authorization": {
        "action": "Microsoft.Storage/storageAccounts/write",
        "scope": "/subscriptions/s",
    },
    "properties": {"statusCode": "OK"},
    "http_request": {"clientIpAddress": "10.0.0.1"},
    "claims": {
        OID_CLAIM: "ALICE-OID",
        UPN_CLAIM: "alice@example.com",
        "appid": "app-1",
        "idtyp": "user",
        "aio": "x" * 200,
        "uti": "token-id",
        "xms_tcdt": "123",
    },
}


def test_slim_drops_duplicated_keys_and_most_claims():
    slim = slim_payload(FULL)
    assert set(slim) == {"description", "authorization", "properties", "http_request", "claims"}
    assert slim["claims"] == {
        "oid": "ALICE-OID",
        "appid": "app-1",
        "idtyp": "user",
        "upn": "alice@example.com",
    }
    assert slim_payload(slim) == slim  # idempotent: the migration can be re-run
    assert slim_payload(None) is None
    assert "claims" not in slim_payload({"description": "no token"})


def test_caller_oid_from_full_and_slim_payloads():
    assert caller_oid_from_payload(FULL) == "alice-oid"
    assert caller_oid_from_payload(slim_payload(FULL)) == "alice-oid"
    assert caller_oid_from_payload({"claims": {"oid": "Short-Form"}}) == "short-form"
    assert caller_oid_from_payload({"claims": "not a dict"}) is None
    assert caller_oid_from_payload(None) is None


def test_insert_derives_caller_oid_unless_set(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        derived = ActivityEvent(
            subscription_id="s",
            event_data_id="e1",
            operation_name="op",
            occurred_at=now,
            raw=slim_payload(FULL),
        )
        explicit = ActivityEvent(
            subscription_id="s",
            event_data_id="e2",
            operation_name="op",
            occurred_at=now,
            raw=FULL,
            caller_oid="given",
        )
        none = ActivityEvent(
            subscription_id="s", event_data_id="e3", operation_name="op", occurred_at=now, raw=None
        )
        db.add_all([derived, explicit, none])
        db.commit()
        assert (derived.caller_oid, explicit.caller_oid, none.caller_oid) == (
            "alice-oid",
            "given",
            None,
        )
