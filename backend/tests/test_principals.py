"""Principal page: access via groups, members, findings, own activity; ⌘K search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.test_registry import (
    MG,
    OWNER,
    READER,
    RG,
    SUB,
    SUB_SCOPE,
    _ra,
    _seed_roles,
    _seed_scan,
    _seed_world,
)

OID_CLAIM = "http://schemas.microsoft.com/identity/claims/objectidentifier"


def _world(client: TestClient) -> int:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
    client.post("/api/settings/registry/rebuild")
    client.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    return scan_id


def _event(
    n: int,
    *,
    oid: str | None,
    op: str,
    caller: str,
    ago_days: float,
    status="Succeeded",
    long_claim: bool = True,
):
    from app.db.models import ActivityEvent

    claims = {}
    if oid:
        claims[OID_CLAIM if long_claim else "oid"] = oid
    return ActivityEvent(
        subscription_id=SUB,
        event_data_id=f"evt-{n}",
        caller=caller,
        caller_type="User",
        operation_name=op,
        status=status,
        occurred_at=datetime.now(UTC) - timedelta(days=ago_days),
        raw={"claims": claims},
    )


def test_page_summarises_access_and_findings(app_with_sqlite: TestClient):
    _world(app_with_sqlite)

    sp = app_with_sqlite.get("/api/principals/SP-1").json()
    assert sp["summary"] == {
        "direct": 1,
        "via_group": 0,
        "high_risk": 1,
        "open_findings": 1,
        "subscriptions": 0,
        "eligible": 0,
    }
    [row] = sp["assignments"]
    assert row["scope"] == MG and row["is_high_risk"] and row["subscription_id"] is None
    assert row["open_findings"] == 1
    [finding] = sp["findings"]
    assert finding["category"] == "high_risk_broad_scope"
    assert finding["principal_id"].lower() == "sp-1"  # full CurrentFindingRead shape
    assert finding["role_name"] == "Owner"

    alice = app_with_sqlite.get("/api/principals/alice-id").json()
    assert alice["summary"]["direct"] == 1 and alice["summary"]["via_group"] == 1
    assert alice["summary"]["subscriptions"] == 1
    assert {a["subscription_name"] for a in alice["assignments"]} == {"prod"}
    via = next(a for a in alice["assignments"] if a["source"] == "group")
    assert via["scope"] == RG and via["via_group_name"] == "grp-readers"


def test_group_page_lists_members(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    grp = app_with_sqlite.get("/api/principals/grp-1").json()
    assert grp["members_total"] == 2
    # Named members first, unnamed after.
    assert [m["member_id"] for m in grp["members"]] == ["alice-id", "bob-id"]
    capped = app_with_sqlite.get("/api/principals/grp-1", params={"members_limit": 1}).json()
    assert len(capped["members"]) == 1 and capped["members_total"] == 2
    assert app_with_sqlite.get("/api/principals/nobody").status_code == 404
    assert grp["members_read"] is True


def test_an_empty_group_is_told_apart_from_an_unreadable_one(app_with_sqlite: TestClient):
    from app.db.models import ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        db.add_all(
            [
                _ra(scan_id, SUB_SCOPE, "subscription", "grp-empty", "Group", OWNER, "grp-empty"),
                _ra(
                    scan_id, SUB_SCOPE, "subscription", "grp-unread", "Group", READER, "grp-unread"
                ),
                ScanGroupResolution(scan_id=scan_id, group_id="grp-empty"),  # read: no members
            ]
        )
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")
    empty = app_with_sqlite.get("/api/principals/grp-empty").json()
    unread = app_with_sqlite.get("/api/principals/grp-unread").json()
    assert (empty["members_total"], empty["members_read"]) == (0, True)
    assert (unread["members_total"], unread["members_read"]) == (0, False)


def test_activity_matches_object_id_claims(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add_all(
            [
                _event(
                    1,
                    oid="ALICE-ID",
                    op="Microsoft.Compute/virtualMachines/read",
                    caller="alice@example.com",
                    ago_days=1,
                ),
                _event(
                    2,
                    oid="alice-id",
                    op="Microsoft.Compute/virtualMachines/read",
                    caller="alice@example.com",
                    ago_days=2,
                    long_claim=False,
                ),
                _event(
                    3,
                    oid="alice-id",
                    op="Microsoft.Storage/storageAccounts/write",
                    caller="alice@example.com",
                    ago_days=3,
                    status="Failed",
                ),
                _event(
                    4,
                    oid="alice-id",
                    op="Microsoft.Old/thing/read",
                    caller="alice@example.com",
                    ago_days=120,
                ),  # outside the window
                _event(
                    5,
                    oid="bob-id",
                    op="Microsoft.Network/read",
                    caller="bob@example.com",
                    ago_days=1,
                ),
                _event(
                    6, oid=None, op="Microsoft.Network/read", caller="alice@example.com", ago_days=1
                ),
            ]
        )
        db.commit()

    a = app_with_sqlite.get("/api/principals/alice-id/activity").json()
    assert a["identities"] == 1
    assert a["event_count"] == 3
    assert a["failed_count"] == 1
    assert a["operation_count"] == 2
    assert a["callers"] == ["alice@example.com"]
    assert [(o["operation"], o["count"]) for o in a["top_operations"]] == [
        ("Microsoft.Compute/virtualMachines/read", 2),
        ("Microsoft.Storage/storageAccounts/write", 1),
    ]
    assert [
        (s["subscription_id"], s["subscription_name"], s["count"]) for s in a["by_subscription"]
    ] == [(SUB, "prod", 3)]
    assert [e["event_data_id"] for e in a["recent"]] == ["evt-1", "evt-2", "evt-3"]

    # Seeded "2 days ago" lands just outside a 2-day window queried later.
    narrow = app_with_sqlite.get("/api/principals/alice-id/activity", params={"days": 2}).json()
    assert narrow["event_count"] == 1

    # A group's activity is its members'.
    g = app_with_sqlite.get("/api/principals/grp-1/activity").json()
    assert g["identities"] == 2 and g["event_count"] == 4

    assert (
        app_with_sqlite.get("/api/principals/alice-id/activity", params={"days": 91}).status_code
        == 422
    )


def test_search_ranks_exact_and_prefix_matches_first(app_with_sqlite: TestClient):
    _world(app_with_sqlite)

    r = app_with_sqlite.get("/api/search", params={"q": "prod"}).json()
    assert r["scopes"][0] == {"id": SUB_SCOPE, "name": "prod", "kind": "subscription"}

    r = app_with_sqlite.get("/api/search", params={"q": "alice"}).json()
    assert [p["id"] for p in r["principals"]] == ["alice-id"]
    assert r["principals"][0]["kind"] == "User"

    r = app_with_sqlite.get("/api/search", params={"q": "reader"}).json()
    assert r["roles"][0]["id"] == READER and r["roles"][0]["name"] == "Reader"
    assert OWNER not in {x["id"] for x in r["roles"]}

    # Too short: nothing. LIKE wildcards are literal.
    assert app_with_sqlite.get("/api/search", params={"q": "a"}).json()["principals"] == []
    assert app_with_sqlite.get("/api/search", params={"q": "%%"}).json()["scopes"] == []
    assert app_with_sqlite.get("/api/search", params={"q": ""}).status_code == 422


def test_membership_comes_from_the_newest_scan_that_resolved_the_group(app_with_sqlite: TestClient):
    """A newer MG snapshot owns the group's grant but could not read its
    membership (Graph 403); an older RG snapshot did. Members must still
    show, and inherit the grant."""
    from app.db.models import GroupMembership, ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        older = _seed_scan(
            db,
            scope=RG,
            level="resource_group",
            offset_s=0,
            hierarchy={RG: {"name": "rg-app", "level": "resource_group", "parent": None}},
        )
        db.add_all(
            [
                _ra(older.id, RG, "resource_group", "grp-1", "Group", READER, "grp-readers"),
                ScanGroupResolution(scan_id=older.id, group_id="grp-1"),
                GroupMembership(
                    scan_id=older.id,
                    group_id="grp-1",
                    member_id="carol-id",
                    member_type="User",
                    member_name="carol@example.com",
                ),
            ]
        )
        newer = _seed_scan(db, offset_s=10)  # MG-rooted, walks RG; no membership rows
        db.add(_ra(newer.id, RG, "resource_group", "grp-1", "Group", READER, "grp-readers"))
        db.commit()
        older_id, newer_id = older.id, newer.id
    app_with_sqlite.post("/api/settings/registry/rebuild")

    grp = app_with_sqlite.get("/api/principals/grp-1").json()
    assert grp["assignments"][0]["scan_id"] == newer_id  # the grant is current from the MG scan
    assert [m["member_id"] for m in grp["members"]] == ["carol-id"]

    # carol is only known from group membership, not as a principal row; the
    # membership link is what the registry list counts.
    rows = app_with_sqlite.get("/api/principals", params={"q": "grp-1"}).json()
    assert rows[0]["assignment_count"] == 1

    from app.core.current_view import CurrentView

    with SessionLocal() as db:
        view = CurrentView(db)
        assert view.membership_scan == {"grp-1": older_id}
        assert view.groups_of({"CAROL-ID"}) == {"carol-id": {"grp-1"}}
