"""Current-view semantics with overlapping snapshots, scope page endpoints, overview.

World: an older MG-rooted scan A walks MG → SUB → RG; a newer SUB-rooted
scan B walks SUB → RG and records the MG only as the source of an
inherited RA. The current picture must take MG from A and SUB/RG from B,
without double counting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

MG = "/providers/microsoft.management/managementgroups/mg-root"
SUB = "/subscriptions/11111111-1111-1111-1111-111111111111"
RG = f"{SUB}/resourcegroups/rg-app"
OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
NAMES = {MG: "Root MG", SUB: "prod", RG: "rg-app"}
LEVELS = {MG: "mgmt_group", SUB: "subscription", RG: "resource_group"}


def _arm(guid: str) -> str:
    return f"/providers/Microsoft.Authorization/roleDefinitions/{guid}"


def _ra(scan_id: int, scope: str, pid: str, name: str, role: str, ptype: str = "User"):
    from app.db.models import RoleAssignment

    return RoleAssignment(
        scan_id=scan_id,
        scope=scope,
        scope_name=NAMES[scope],
        scope_level=LEVELS[scope],
        principal_id=pid,
        principal_name=name,
        principal_type=ptype,
        role_name={OWNER: "Owner", READER: "Reader"}[role],
        role_type="BuiltInRole",
        role_definition_id=_arm(role),
    )


def _scan(db, root: str, hierarchy: dict, offset_s: int):
    from app.db.models import Scan

    at = datetime.now(UTC) + timedelta(seconds=offset_s)
    scan = Scan(
        scope=root,
        scope_level=LEVELS[root],
        status="done",
        created_at=at,
        finished_at=at,
        hierarchy=hierarchy,
    )
    db.add(scan)
    db.flush()
    return scan


def _world(client: TestClient) -> tuple[int, int]:
    from app.db.models import RoleDefinition
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.add_all(
            [
                RoleDefinition(
                    role_definition_id=OWNER,
                    role_name="Owner",
                    role_type="BuiltInRole",
                    permissions=[{"actions": ["*"], "notActions": []}],
                ),
                RoleDefinition(
                    role_definition_id=READER,
                    role_name="Reader",
                    role_type="BuiltInRole",
                    permissions=[{"actions": ["*/read"], "notActions": []}],
                ),
            ]
        )
        a = _scan(
            db,
            MG,
            {
                MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},
                SUB: {"name": "prod", "level": "subscription", "parent": MG},
                RG: {"name": "rg-app", "level": "resource_group", "parent": SUB},
            },
            offset_s=-3600,
        )
        db.add_all(
            [
                _ra(a.id, MG, "sp-1", "sp-one", OWNER, "ServicePrincipal"),
                _ra(a.id, SUB, "alice", "alice@example.com", READER),
                _ra(a.id, RG, "carol", "carol@example.com", READER),
            ]
        )
        b = _scan(
            db,
            SUB,
            {
                MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},  # ancestor only
                SUB: {"name": "prod", "level": "subscription", "parent": None},
                RG: {"name": "rg-app", "level": "resource_group", "parent": SUB},
            },
            offset_s=0,
        )
        db.add_all(
            [
                _ra(b.id, MG, "sp-1", "sp-one", OWNER, "ServicePrincipal"),  # inherited copy
                _ra(b.id, SUB, "bob", "bob@example.com", READER),
                _ra(b.id, RG, "carol", "carol@example.com", READER),
            ]
        )
        db.commit()
        ids = (a.id, b.id)
    assert client.post("/api/settings/registry/rebuild").status_code == 200
    for sid in ids:
        assert client.post(f"/api/iam-access/scans/{sid}/findings/recompute").status_code == 200
    return ids


# --------------------------------------------------------------------------------------
# Registry pointers + current set
# --------------------------------------------------------------------------------------


def test_only_walked_nodes_claim_a_snapshot(app_with_sqlite: TestClient):
    from app.db.models import Scope
    from app.db.session import SessionLocal

    a, b = _world(app_with_sqlite)
    with SessionLocal() as db:
        assert db.get(Scope, MG).latest_scan_id == a  # B only saw MG as an ancestor
        assert db.get(Scope, SUB).latest_scan_id == b
        assert db.get(Scope, RG).latest_scan_id == b
        assert db.get(Scope, SUB).parent_id == MG  # structural link from A survives


def test_ancestor_only_node_is_registered_without_pointer(app_with_sqlite: TestClient):
    from app.core.registry import upsert_scopes_from_scan
    from app.db.models import Scan, Scope
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan = _scan(
            db,
            SUB,
            {
                MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},
                SUB: {"name": "prod", "level": "subscription", "parent": None},
            },
            offset_s=0,
        )
        db.commit()
        upsert_scopes_from_scan(db, db.get(Scan, scan.id))
        db.commit()
        mg = db.get(Scope, MG)
        assert mg is not None and mg.name == "Root MG" and mg.latest_scan_id is None
        assert db.get(Scope, SUB).latest_scan_id == scan.id


def test_tree_counts_current_set_without_double_counting(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    tree = app_with_sqlite.get("/api/scopes/tree").json()
    mg = tree[0]
    assert mg["id"] == MG
    # sp-1@MG (from A) + bob@SUB + carol@RG (from B). alice is superseded.
    assert mg["stats"]["direct_assignments"] == 1
    assert mg["stats"]["assignments_below"] == 3
    assert mg["stats"]["high_risk"] == 1
    sub = mg["children"][0]
    assert sub["stats"]["direct_assignments"] == 1 and sub["stats"]["assignments_below"] == 2


def test_principal_counts_ignore_superseded_and_ancestor_copies(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    rows = {r["id"]: r for r in app_with_sqlite.get("/api/principals").json()}
    assert rows["sp-1"]["assignment_count"] == 1  # A's and B's copies are one grant
    assert rows["alice"]["assignment_count"] == 0  # removed by B
    assert rows["bob"]["assignment_count"] == 1
    detail = app_with_sqlite.get("/api/principals/sp-1").json()
    assert len(detail["assignments"]) == 1
    assert [f["category"] for f in detail["findings"]] == ["high_risk_broad_scope"]


# --------------------------------------------------------------------------------------
# Scope page
# --------------------------------------------------------------------------------------


def test_scope_assignments_split_by_source(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    body = app_with_sqlite.get(
        "/api/scopes/assignments", params={"scope": SUB.lstrip("/").upper()}
    ).json()
    assert body["scope_id"] == SUB
    assert body["counts"] == {"direct": 1, "inherited": 1, "below": 1}
    got = [(r["source"], r["principal_id"], r["role_name"]) for r in body["rows"]]
    assert got == [
        ("direct", "bob", "Reader"),
        ("inherited", "sp-1", "Owner"),
        ("below", "carol", "Reader"),
    ]
    owner = body["rows"][1]
    assert owner["role_risk"] == "high"
    assert [f["category"] for f in owner["findings"]] == ["high_risk_broad_scope"]
    assert owner["findings"][0]["status"] == "open"

    only_inherited = app_with_sqlite.get(
        "/api/scopes/assignments", params={"scope": SUB, "source": "inherited"}
    ).json()
    assert [r["principal_id"] for r in only_inherited["rows"]] == ["sp-1"]
    assert only_inherited["counts"]["direct"] == 1  # counts are unfiltered

    mg = app_with_sqlite.get("/api/scopes/assignments", params={"scope": MG}).json()
    assert mg["counts"] == {"direct": 1, "inherited": 0, "below": 2}

    assert (
        app_with_sqlite.get(
            "/api/scopes/assignments", params={"scope": "subscriptions/nope"}
        ).status_code
        == 404
    )


def test_scope_findings_include_inherited_and_honour_decisions(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    body = app_with_sqlite.get("/api/scopes/findings", params={"scope": SUB}).json()
    assert body["summary"]["total"] == 1  # B's copy of the Owner grant is not current
    f = body["findings"][0]
    assert (f["category"], f["source"], f["scope_name"]) == (
        "high_risk_broad_scope",
        "inherited",
        "Root MG",
    )

    app_with_sqlite.put(
        f"/api/iam-access/findings/{f['fingerprint']}/decision",
        json={"decision": "accepted_risk", "note": "break-glass SP"},
    )
    after = app_with_sqlite.get("/api/scopes/findings", params={"scope": SUB}).json()
    assert after["summary"]["open"] == 0
    assert after["findings"][0]["status"] == "accepted_risk"
    assert (
        app_with_sqlite.get("/api/scopes/findings", params={"scope": SUB, "status": "open"}).json()[
            "findings"
        ]
        == []
    )

    tree = app_with_sqlite.get("/api/scopes/tree").json()
    assert tree[0]["stats"]["open_findings"] == 0


def test_scope_history_lists_own_and_ancestor_snapshots(app_with_sqlite: TestClient):
    a, b = _world(app_with_sqlite)
    sub = app_with_sqlite.get("/api/scopes/history", params={"scope": SUB}).json()
    assert [(s["id"], s["is_current"]) for s in sub] == [(b, True), (a, False)]
    assert sub[0]["scope_name"] == "prod" and sub[1]["scope_name"] == "Root MG"
    assert sub[0]["role_assignment_count"] == 3
    assert sub[0]["previous_done_id"] is None  # first scan of the SUB root
    mg = app_with_sqlite.get("/api/scopes/history", params={"scope": MG}).json()
    assert [s["id"] for s in mg] == [a]


def test_scope_routes_do_not_shadow_detail(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    detail = app_with_sqlite.get(f"/api/scopes{RG}").json()
    assert detail["scope"]["id"] == RG
    assert [c["name"] for c in detail["ancestors"]] == ["Root MG", "prod"]


# --------------------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------------------


def test_overview_kpis_attention_roles_freshness_jobs(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    body = app_with_sqlite.get("/api/dashboard/overview").json()
    assert body["kpis"] == {
        "assignments": 3,
        "principals": 3,
        "high_risk_broad": 1,
        "unused": 0,
        "unresolved_principals": 0,
        "open_findings": 1,
        "added_30d": 0,
        "removed_30d": 0,
        "needs_look": 1,
        "by_level": {"access": 1, "keys": 0, "change": 0, "data": 0, "view": 2, "unknown": 0},
    }
    assert [a["category"] for a in body["attention"]] == ["high_risk_broad_scope"]
    assert body["attention"][0]["grant_id"] and body["attention"][0]["role_name"] == "Owner"
    assert body["access_as_of"] is not None
    assert body["activity_up_to"] is None  # no events collected
    assert body["attention"][0]["scope_name"] == "Root MG"
    assert [(r["role_name"], r["count"]) for r in body["top_roles"]] == [
        ("Reader", 2),
        ("Owner", 1),
    ]
    assert body["top_roles"][1]["role_risk"] == "high"
    assert body["freshness"]["subscriptions_total"] == 1
    assert body["freshness"]["subscriptions_fresh"] == 1
    assert [j["kind"] for j in body["jobs"]] == ["snapshot", "snapshot"]
    assert body["jobs"][0]["target_name"] == "prod"
    assert body["findings_pending"] == []  # _world recomputed both


def test_overview_reports_snapshots_without_findings(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan = _scan(db, SUB, {SUB: {"name": "prod", "level": "subscription", "parent": None}}, 0)
        db.add(_ra(scan.id, SUB, "bob", "b", READER))
        db.commit()
        scan_id = scan.id
    app_with_sqlite.post("/api/settings/registry/rebuild")
    assert app_with_sqlite.get("/api/dashboard/overview").json()["findings_pending"] == [scan_id]
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    assert app_with_sqlite.get("/api/dashboard/overview").json()["findings_pending"] == []


def test_overview_changes_since_previous_snapshot(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    hierarchy = {
        MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},
        SUB: {"name": "prod", "level": "subscription", "parent": MG},
    }
    with SessionLocal() as db:
        old = _scan(db, MG, hierarchy, offset_s=-60)
        db.add_all([_ra(old.id, SUB, "alice", "a", READER), _ra(old.id, SUB, "bob", "b", READER)])
        new = _scan(db, MG, hierarchy, offset_s=0)
        db.add_all([_ra(new.id, SUB, "bob", "b", READER), _ra(new.id, SUB, "dave", "d", READER)])
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")
    from app.core.changes import rebuild_changes

    with SessionLocal() as db:
        rebuild_changes(db)  # the changes feed, as the scheduler fills it
    kpis = app_with_sqlite.get("/api/dashboard/overview").json()["kpis"]
    assert (kpis["added_30d"], kpis["removed_30d"], kpis["assignments"]) == (1, 1, 2)


def test_overview_empty_database(app_with_sqlite: TestClient):
    body = app_with_sqlite.get("/api/dashboard/overview").json()
    assert body["kpis"]["assignments"] == 0
    assert body["attention"] == [] and body["jobs"] == []
    assert body["freshness"]["subscriptions_total"] == 0


# --------------------------------------------------------------------------------------
# Phase 3: cross-scope findings, bulk decisions, scope activity
# --------------------------------------------------------------------------------------


def _add_open_finding_world(client: TestClient) -> None:
    """_world plus an unresolved principal (no name) at RG so there are 2+ findings."""
    from app.db.models import Scan
    from app.db.session import SessionLocal

    _, b = _world(client)
    with SessionLocal() as db:
        db.add(_ra(b, RG, "ghost", None, OWNER, "ServicePrincipal"))
        db.commit()
        assert db.get(Scan, b) is not None
    client.post("/api/settings/registry/rebuild")
    client.post(f"/api/iam-access/scans/{b}/findings/recompute")


def test_global_findings_once_per_grant_with_filters_and_paging(app_with_sqlite: TestClient):
    _add_open_finding_world(app_with_sqlite)
    body = app_with_sqlite.get("/api/findings").json()
    cats = [(f["category"], f["principal_id"]) for f in body["findings"]]
    # sp-1's Owner@MG is in both snapshots but current only from A: once.
    assert cats.count(("high_risk_broad_scope", "sp-1")) == 1
    assert ("unresolved_principal", "ghost") in cats
    assert body["summary"]["total"] == body["total"] == len(body["findings"])
    first = body["findings"][0]
    assert (
        first["severity"] == "high" and first["category"] == "unresolved_principal"
    )  # identity first
    assert {"scope_name", "scope_level", "principal_type", "role_name"} <= first.keys()

    only_high = app_with_sqlite.get("/api/findings", params={"severity": "high"}).json()
    assert all(f["severity"] == "high" for f in only_high["findings"])
    assert only_high["summary"]["total"] == body["summary"]["total"]  # summary unfiltered

    by_q = app_with_sqlite.get("/api/findings", params={"q": "SP-ONE"}).json()
    assert {f["principal_id"] for f in by_q["findings"]} == {"sp-1"}

    page = app_with_sqlite.get("/api/findings", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == body["total"] and len(page["findings"]) == 1
    assert page["findings"][0]["id"] == body["findings"][1]["id"]


def test_global_findings_page_by_grant(app_with_sqlite: TestClient):
    _add_open_finding_world(app_with_sqlite)
    flat = app_with_sqlite.get("/api/findings").json()
    grants = {(f["scan_id"], f["role_assignment_id"] or f["fingerprint"]) for f in flat["findings"]}
    assert flat["summary"]["grants"] == len(grants)
    assert flat["summary"]["open_grants"] == len(grants)  # nothing decided yet

    body = app_with_sqlite.get("/api/findings", params={"per": "grant"}).json()
    assert body["total"] == len(grants)
    assert sorted(f["id"] for f in body["findings"]) == sorted(f["id"] for f in flat["findings"])

    # A page of one grant carries every finding on that grant, none of another.
    seen = []
    for offset in range(len(grants)):
        page = app_with_sqlite.get(
            "/api/findings", params={"per": "grant", "limit": 1, "offset": offset}
        ).json()
        keys = {
            (f["scan_id"], f["role_assignment_id"] or f["fingerprint"]) for f in page["findings"]
        }
        assert len(keys) == 1 and page["total"] == len(grants)
        seen.extend(f["id"] for f in page["findings"])
    assert sorted(seen) == sorted(f["id"] for f in flat["findings"])
    assert app_with_sqlite.get("/api/findings", params={"per": "role"}).status_code == 422


def test_bulk_decisions_apply_to_all_and_feed_decided_filter(app_with_sqlite: TestClient):
    _add_open_finding_world(app_with_sqlite)
    fps = [f["fingerprint"] for f in app_with_sqlite.get("/api/findings").json()["findings"]]
    r = app_with_sqlite.post(
        "/api/iam-access/findings/decisions",
        json={"fingerprints": fps + fps[:1], "decision": "reviewed", "note": "Q4 review"},
    )
    assert r.status_code == 200 and r.json() == {"updated": len(fps)}  # duplicates collapsed
    after = app_with_sqlite.get("/api/findings").json()
    assert after["summary"]["open"] == 0
    assert app_with_sqlite.get("/api/findings", params={"status": "decided"}).json()[
        "total"
    ] == len(fps)
    assert app_with_sqlite.get("/api/findings", params={"status": "open"}).json()["total"] == 0

    bad = app_with_sqlite.post(
        "/api/iam-access/findings/decisions", json={"fingerprints": fps, "decision": "snoozed"}
    )
    assert bad.status_code == 400
    assert (
        app_with_sqlite.post(
            "/api/iam-access/findings/decisions", json={"fingerprints": [], "decision": "reviewed"}
        ).status_code
        == 422
    )


def test_scope_activity_volume_pulls_unattributed_and_most_active(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal

    _, b = _world(app_with_sqlite)
    sub_guid = SUB.rsplit("/", 1)[-1]
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                ActivityPull(
                    subscription_id=sub_guid,
                    start_time=now - timedelta(days=7),
                    end_time=now,
                    status="done",
                    finished_at=now,
                    events_persisted=3,
                ),
                ActivityPull(
                    subscription_id=sub_guid,
                    start_time=now - timedelta(days=1),
                    end_time=now,
                    status="failed",
                    error="429",
                ),
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id="e1",
                    caller="bob@example.com",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{SUB}/resourcegroups/rg-app/providers/x/y",
                    status="Succeeded",
                    occurred_at=now - timedelta(days=1),
                ),
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id="e2",
                    caller="bob@example.com",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{SUB}/resourcegroups/rg-app/providers/x/y",
                    status="Succeeded",
                    occurred_at=now - timedelta(days=2),
                ),
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id="e3",
                    caller="mallory@evil.test",
                    operation_name="Microsoft.Compute/virtualMachines/write",
                    resource_id=None,
                    status="Succeeded",
                    occurred_at=now - timedelta(days=3),
                ),
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id="old",
                    caller="bob@example.com",
                    operation_name="x/read",
                    resource_id=None,
                    status="Succeeded",
                    occurred_at=now - timedelta(days=200),
                ),
            ]
        )
        db.commit()
    rc = app_with_sqlite.post(f"/api/iam-access/scans/{b}/findings/recompute")
    assert rc.status_code == 200, rc.text

    body = app_with_sqlite.get("/api/scopes/activity", params={"scope": SUB}).json()
    assert [s["id"] for s in body["subscriptions"]] == [SUB]
    sub = body["subscriptions"][0]
    assert sub["events_90d"] == 3  # the 200-day-old event is outside the window
    assert sub["last_pull"]["status"] == "failed"
    assert sub["last_successful_pull"]["status"] == "done"
    assert body["snapshot_id"] == b
    assert (body["events_considered"], body["events_attributed"]) == (3, 2)
    assert [(c["caller"], c["event_count"]) for c in body["unattributed_callers"]] == [
        ("mallory@evil.test", 1)
    ]
    assert [(a["principal_id"], a["event_count"]) for a in body["most_active"]] == [("bob", 2)]

    # An MG lists every subscription below it; RG resolves to its subscription.
    assert [
        s["id"]
        for s in app_with_sqlite.get("/api/scopes/activity", params={"scope": MG}).json()[
            "subscriptions"
        ]
    ] == [SUB]
    assert [
        s["id"]
        for s in app_with_sqlite.get("/api/scopes/activity", params={"scope": RG}).json()[
            "subscriptions"
        ]
    ] == [SUB]


def test_current_view_is_built_once_per_session_and_forgotten_on_writes(
    app_with_sqlite: TestClient,
):
    from app.core.current_view import current_view
    from app.db.models import AppSetting
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        first = current_view(db)
        assert current_view(db) is first  # shared within one request
        db.add(AppSetting(key="t-view", value={"x": 1}))
        db.flush()
        second = current_view(db)
        assert second is not first  # a write makes it stale
        db.commit()
        assert current_view(db) is not second
    with SessionLocal() as other:
        assert current_view(other) is not first  # never shared between sessions
