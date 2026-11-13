"""Audit log middleware + endpoint smoke tests.

Confirms mutations get recorded with the right method / path / status
and that read-only GETs don't.
"""

from fastapi.testclient import TestClient


def test_mutations_are_logged(app_with_sqlite: TestClient):
    # Anonymous PUT (auth is off in test) — should record one event.
    app_with_sqlite.put(
        "/api/settings/auth",
        json={"enabled": False, "tenant_id": "t", "client_id": "c"},
    )
    response = app_with_sqlite.get("/api/audit/events")
    assert response.status_code == 200
    events = response.json()
    assert any(e["method"] == "PUT" and e["path"] == "/api/settings/auth" for e in events)
    last = events[0]
    assert last["actor"] == "anonymous"
    assert 200 <= last["status_code"] < 600


def test_gets_are_not_logged(app_with_sqlite: TestClient):
    # Read-only call shouldn't add a row.
    before = app_with_sqlite.get("/api/audit/events").json()
    app_with_sqlite.get("/api/iam-access/scans")
    after = app_with_sqlite.get("/api/audit/events").json()
    # Listing the audit log is itself a GET, so the counts should
    # match (no new entries between the two reads).
    assert len(after) == len(before)


def test_failed_mutations_are_still_logged(app_with_sqlite: TestClient):
    # Hit a mutation route with a body the schema rejects → 422.
    app_with_sqlite.put("/api/settings/auth", json={"enabled": "not-a-bool"})
    events = app_with_sqlite.get("/api/audit/events").json()
    bad = [e for e in events if e["status_code"] >= 400]
    assert bad, "422 mutation should still appear in the audit log"


def test_the_callers_address_is_recorded(app_with_sqlite: TestClient):
    # TestClient connects as host "testclient"; behind nginx this is the address
    # nginx saw, passed on in X-Forwarded-For (core/audit.py).
    app_with_sqlite.put("/api/settings/auth", json={"enabled": False})
    last = app_with_sqlite.get("/api/audit/events").json()[0]
    assert last["path"] == "/api/settings/auth" and last["client_ip"] == "testclient"
