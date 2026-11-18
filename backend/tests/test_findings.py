"""Findings derivation, persistence, review decisions, coverage cache.

Scans are seeded directly (status ``done``) and findings are produced
through ``POST /scans/{id}/findings/recompute`` so the DB-facing path is
exercised end to end on SQLite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import coverage_of

SUB = "11111111-1111-1111-1111-111111111111"
SUB_SCOPE = f"/subscriptions/{SUB}"
RG_SCOPE = f"{SUB_SCOPE}/resourcegroups/rg-app"

OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
CUSTOM = "c0000000-0000-0000-0000-000000000001"


def _arm(guid: str) -> str:
    return f"/providers/Microsoft.Authorization/roleDefinitions/{guid}"


def _seed_roles(db) -> None:
    from app.db.models import RoleDefinition

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
            RoleDefinition(
                role_definition_id=CUSTOM,
                role_name="custom_reader_plus",
                role_type="CustomRole",
                permissions=[{"actions": ["*/read", "Microsoft.Storage/storageAccounts/write"]}],
            ),
        ]
    )


def _seed_scan(db, *, created_at: datetime | None = None):
    from app.db.models import Scan

    scan = Scan(
        scope=SUB_SCOPE,
        scope_level="subscription",
        status="done",
        created_at=created_at or datetime.now(UTC),
        hierarchy={
            SUB_SCOPE: {"name": "prod", "level": "subscription", "parent": None},
            RG_SCOPE: {"name": "rg-app", "level": "resource_group", "parent": SUB_SCOPE},
        },
    )
    db.add(scan)
    db.flush()
    return scan


def _ra(scan_id: int, scope: str, level: str, principal: str, ptype: str, role: str, **kw):
    from app.db.models import RoleAssignment

    names = {OWNER: "Owner", READER: "Reader", CUSTOM: "custom_reader_plus"}
    types = {OWNER: "BuiltInRole", READER: "BuiltInRole", CUSTOM: "CustomRole"}
    # Real scans persist the friendly name per RA row (the UI shows
    # "prod", not the GUID); finding titles rely on it.
    scope_names = {SUB_SCOPE: "prod", RG_SCOPE: "rg-app"}
    return RoleAssignment(
        scan_id=scan_id,
        scope=scope,
        scope_name=scope_names.get(scope),
        scope_level=level,
        principal_id=principal,
        principal_type=ptype,
        role_name=names[role],
        role_type=types[role],
        role_definition_id=_arm(role),
        **kw,
    )


def _seed_baseline(db) -> int:
    """Owner@sub (sp-1, named) · Reader@rg (sp-1, redundant) · Reader@rg (u-1,
    unresolved) · Reader@sub (g-1, membership unknown)."""
    _seed_roles(db)
    scan = _seed_scan(db)
    db.add_all(
        [
            _ra(
                scan.id,
                SUB_SCOPE,
                "subscription",
                "sp-1",
                "ServicePrincipal",
                OWNER,
                principal_name="sp-one",
            ),
            _ra(
                scan.id,
                RG_SCOPE,
                "resource_group",
                "sp-1",
                "ServicePrincipal",
                READER,
                principal_name="sp-one",
            ),
            _ra(scan.id, RG_SCOPE, "resource_group", "u-1", "User", READER),
            _ra(
                scan.id,
                SUB_SCOPE,
                "subscription",
                "g-1",
                "Group",
                READER,
                principal_name="grp-readers",
            ),
        ]
    )
    db.commit()
    return scan.id


def _categories(body: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in body["findings"]:
        out.setdefault(f["category"], []).append(f["title"])
    return out


# --------------------------------------------------------------------------------------


def test_fingerprint_normalises_inputs():
    from app.core.findings import fingerprint

    a = fingerprint("/Subscriptions/ABC", _arm(OWNER), "SP-1", "unused")
    b = fingerprint("/subscriptions/abc", f"/subscriptions/x{_arm(OWNER)}", "sp-1", "unused")
    assert a == b
    assert len(a) == 40
    assert fingerprint("/subscriptions/abc", _arm(OWNER), "sp-1", "redundant") != a


def test_recompute_derives_expected_categories(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)

    r = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    assert r.status_code == 200, r.text
    body = r.json()
    cats = _categories(body)

    assert cats["high_risk_broad_scope"] == ["Owner → sp-one on prod"]
    assert cats["unresolved_principal"] == ["Reader → u-1 on rg-app"]
    assert cats["redundant"] == ["Reader → sp-one on rg-app"]
    assert cats["membership_unknown"] == ["Reader → grp-readers on prod"]
    # No activity pulled for the sub → no usage-based findings.
    assert "unused" not in cats and "over_broad" not in cats
    # No previous scan → no drift.
    assert "drift_added" not in cats

    by_title = {f["title"]: f for f in body["findings"]}
    assert by_title["Owner → sp-one on prod"]["severity"] == "high"
    assert by_title["Reader → sp-one on rg-app"]["severity"] == "low"
    assert (
        by_title["Reader → sp-one on rg-app"]["evidence"]["covered_by"][0]["role_name"] == "Owner"
    )
    assert all(f["status"] == "open" for f in body["findings"])

    assert body["computed_at"] is not None
    assert body["window_days"] == 90
    assert body["previous_scan_id"] is None
    assert body["summary"]["total"] == 4
    assert body["summary"]["open"] == 4
    assert body["summary"]["by_severity"] == {"high": 1, "medium": 2, "low": 1}

    # Findings sort high → medium → low.
    assert [f["severity"] for f in body["findings"]] == ["high", "medium", "medium", "low"]


def test_recompute_is_idempotent_and_fingerprints_are_stable(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)

    first = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    second = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    assert {f["fingerprint"] for f in first["findings"]} == {
        f["fingerprint"] for f in second["findings"]
    }
    assert second["summary"]["total"] == 4  # replaced, not appended


def test_list_filters_only_narrow_findings_not_summary(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")

    body = app_with_sqlite.get(
        f"/api/iam-access/scans/{scan_id}/findings", params={"severity": "high"}
    ).json()
    assert [f["category"] for f in body["findings"]] == ["high_risk_broad_scope"]
    assert body["summary"]["total"] == 4

    body = app_with_sqlite.get(
        f"/api/iam-access/scans/{scan_id}/findings", params={"category": "redundant"}
    ).json()
    assert len(body["findings"]) == 1

    assert (
        app_with_sqlite.get(
            f"/api/iam-access/scans/{scan_id}/findings", params={"severity": "bogus"}
        ).status_code
        == 422
    )


def test_findings_null_computed_at_before_first_compute(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
    body = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/findings").json()
    assert body["computed_at"] is None
    assert body["findings"] == []
    assert body["summary"]["total"] == 0


def test_recompute_rejects_scan_that_is_not_done(app_with_sqlite: TestClient):
    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-pending"}).json()[
        "id"
    ]
    r = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    assert r.status_code == 400
    assert app_with_sqlite.post("/api/iam-access/scans/9999/findings/recompute").status_code == 404


def test_decision_roundtrip_status_and_snooze_expiry(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
    body = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    fp = next(f["fingerprint"] for f in body["findings"] if f["category"] == "redundant")

    r = app_with_sqlite.put(
        f"/api/iam-access/findings/{fp}/decision",
        json={"decision": "reviewed", "note": "ok for now"},
    )
    assert r.status_code == 200
    assert r.json()["actor"] == "anonymous"  # auth disabled in tests
    assert r.json()["note"] == "ok for now"

    listed = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/findings").json()
    item = next(f for f in listed["findings"] if f["fingerprint"] == fp)
    assert item["status"] == "reviewed"
    assert item["decision"]["decision"] == "reviewed"
    assert listed["summary"]["open"] == 3
    assert listed["summary"]["by_status"] == {"open": 3, "reviewed": 1}

    # Snooze in the past reads as open; in the future as snoozed.
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    app_with_sqlite.put(
        f"/api/iam-access/findings/{fp}/decision",
        json={"decision": "snoozed", "snoozed_until": past},
    )
    assert _status_of(app_with_sqlite, scan_id, fp) == "open"
    app_with_sqlite.put(
        f"/api/iam-access/findings/{fp}/decision",
        json={"decision": "snoozed", "snoozed_until": future},
    )
    assert _status_of(app_with_sqlite, scan_id, fp) == "snoozed"
    assert (
        app_with_sqlite.put(
            f"/api/iam-access/findings/{fp}/decision", json={"decision": "snoozed"}
        ).status_code
        == 400
    )

    # Status filter uses the effective status.
    only_snoozed = app_with_sqlite.get(
        f"/api/iam-access/scans/{scan_id}/findings", params={"status": "snoozed"}
    ).json()["findings"]
    assert [f["fingerprint"] for f in only_snoozed] == [fp]

    assert app_with_sqlite.delete(f"/api/iam-access/findings/{fp}/decision").status_code == 204
    assert app_with_sqlite.delete(f"/api/iam-access/findings/{fp}/decision").status_code == 404
    assert _status_of(app_with_sqlite, scan_id, fp) == "open"


def _status_of(client: TestClient, scan_id: int, fp: str) -> str:
    body = client.get(f"/api/iam-access/scans/{scan_id}/findings").json()
    return next(f["status"] for f in body["findings"] if f["fingerprint"] == fp)


def test_decision_survives_rescan_and_drift_is_detected(app_with_sqlite: TestClient):
    """A later snapshot of the same scope re-raises the same fingerprint with
    the earlier decision attached, and flags only the newly added grant."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        old_id = _seed_baseline(db)
    old = app_with_sqlite.post(f"/api/iam-access/scans/{old_id}/findings/recompute").json()
    fp_redundant = next(f["fingerprint"] for f in old["findings"] if f["category"] == "redundant")
    app_with_sqlite.put(
        f"/api/iam-access/findings/{fp_redundant}/decision",
        json={"decision": "accepted_risk", "note": "legacy automation"},
    )

    # New snapshot: same four grants + one new Owner grant on the RG.
    with SessionLocal() as db:
        scan = _seed_scan(db, created_at=datetime.now(UTC) + timedelta(seconds=1))
        db.add_all(
            [
                _ra(
                    scan.id,
                    SUB_SCOPE,
                    "subscription",
                    "sp-1",
                    "ServicePrincipal",
                    OWNER,
                    principal_name="sp-one",
                ),
                _ra(
                    scan.id,
                    RG_SCOPE,
                    "resource_group",
                    "sp-1",
                    "ServicePrincipal",
                    READER,
                    principal_name="sp-one",
                ),
                _ra(scan.id, RG_SCOPE, "resource_group", "u-1", "User", READER),
                _ra(
                    scan.id,
                    SUB_SCOPE,
                    "subscription",
                    "g-1",
                    "Group",
                    READER,
                    principal_name="grp-readers",
                ),
                _ra(
                    scan.id,
                    RG_SCOPE,
                    "resource_group",
                    "sp-new",
                    "ServicePrincipal",
                    OWNER,
                    principal_name="sp-new",
                ),
            ]
        )
        db.commit()
        new_id = scan.id

    new = app_with_sqlite.post(f"/api/iam-access/scans/{new_id}/findings/recompute").json()
    assert new["previous_scan_id"] == old_id

    carried = next(f for f in new["findings"] if f["fingerprint"] == fp_redundant)
    assert carried["scan_id"] == new_id
    assert carried["status"] == "accepted_risk"
    assert carried["decision"]["note"] == "legacy automation"

    drift = _categories(new).get("drift_added", [])
    assert drift == ["Owner → sp-new on rg-app"]
    drift_item = next(f for f in new["findings"] if f["category"] == "drift_added")
    assert drift_item["severity"] == "high"  # Owner is high-risk


def test_unused_requires_activity_for_the_subscription(app_with_sqlite: TestClient):
    """With events on file for the sub and none attributable to sp-1, Owner is unused."""
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
        db.add(
            ActivityEvent(
                subscription_id=SUB,
                event_data_id="evt-1",
                caller="someone-else@example.com",
                caller_type="User",
                operation_name="Microsoft.Compute/virtualMachines/write",
                resource_id=f"{RG_SCOPE}/providers/microsoft.compute/virtualmachines/vm1",
                status="Succeeded",
                occurred_at=datetime.now(UTC) - timedelta(days=3),
            )
        )
        db.commit()

    body = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    cats = _categories(body)
    assert "Owner → sp-one on prod" in cats["unused"]
    unused_owner = next(
        f
        for f in body["findings"]
        if f["category"] == "unused" and f["title"] == "Owner → sp-one on prod"
    )
    assert unused_owner["severity"] == "high"
    assert unused_owner["evidence"]["coverage_pct"] == 0
    # The group with unknown membership must NOT be flagged unused.
    assert "Reader → grp-readers on prod" not in cats["unused"]


def test_recompute_caches_the_coverage(app_with_sqlite: TestClient):
    from app.db.models import ScanCoverage
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)

    live = coverage_of(scan_id)
    assert live["scan_id"] == scan_id and len(live["rows"]) == 4
    with SessionLocal() as db:
        assert db.get(ScanCoverage, scan_id) is None  # building it does not store it

    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    with SessionLocal() as db:
        cache = db.get(ScanCoverage, scan_id)
        assert cache is not None and cache.window_days == 90
        assert len(cache.payload["rows"]) == 4


def test_run_scan_produces_findings_and_cache(app_with_sqlite: TestClient, monkeypatch):
    """The worker hook: a scan that completes gets its findings computed."""
    from app.db.models import Finding, RoleAssignment, ScanCoverage
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": SUB}).json()["id"]
    with SessionLocal() as db:
        _seed_roles(db)
        db.commit()

    def collect(scope, scope_level, credential, on_progress):
        return (
            [
                RoleAssignment(
                    scope=scope,
                    scope_level=scope_level,
                    principal_id="ghost",
                    principal_type="ServicePrincipal",
                    role_name="Owner",
                    role_type="BuiltInRole",
                    role_definition_id=_arm(OWNER),
                )
            ],
            {scope: {"name": "prod", "level": scope_level, "parent": None}},
            {},
            {},
        )

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", collect)
    tool.run_scan(scan_id)

    with SessionLocal() as db:
        assert db.get(ScanCoverage, scan_id) is not None
        cats = {f.category for f in db.query(Finding).filter_by(scan_id=scan_id)}
    assert cats == {"high_risk_broad_scope", "unresolved_principal"}


def test_delete_scan_removes_findings_and_cache(app_with_sqlite: TestClient):
    from app.db.models import Finding, ScanCoverage
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    assert app_with_sqlite.delete(f"/api/iam-access/scans/{scan_id}").status_code == 204
    with SessionLocal() as db:
        assert db.query(Finding).filter_by(scan_id=scan_id).count() == 0
        assert db.get(ScanCoverage, scan_id) is None


def test_coverage_aggregates_events_and_correlates_by_claims(app_with_sqlite: TestClient):
    """Events are grouped in SQL; JWT object-id claims (long and short form,
    any case) attribute an event even when the caller name doesn't match."""
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    long_claim = "http://schemas.microsoft.com/identity/claims/objectidentifier"
    vm = f"{RG_SCOPE}/providers/Microsoft.Compute/virtualMachines/VM1"
    now = datetime.now(UTC)

    def ev(i, caller, op, claims=None, days_ago=1):
        return ActivityEvent(
            subscription_id=SUB,
            event_data_id=f"e{i}",
            caller=caller,
            caller_type="ServicePrincipal",
            operation_name=op,
            resource_id=vm,
            status="Succeeded",
            occurred_at=now - timedelta(days=days_ago),
            raw={"claims": claims} if claims else None,
        )

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
        db.add_all(
            [
                ev(
                    1,
                    "display-name-not-in-graph",
                    "Microsoft.Compute/virtualMachines/write",
                    {long_claim: "SP-1"},
                    3,
                ),
                ev(
                    2,
                    "display-name-not-in-graph",
                    "Microsoft.Compute/virtualMachines/write",
                    {long_claim: "sp-1"},
                    2,
                ),
                ev(3, None, "Microsoft.Compute/virtualMachines/write", {long_claim: "sp-1"}, 1),
                ev(4, "x", "Microsoft.Compute/virtualMachines/read", {"oid": "sp-1"}, 5),
                ev(5, "stranger@example.com", "Microsoft.Compute/virtualMachines/write", None, 1),
                ev(6, "stranger@example.com", "Microsoft.Compute/virtualMachines/write", None, 1),
            ]
        )
        db.commit()

    cov = coverage_of(scan_id)
    assert (cov["events_considered"], cov["events_attributed"], cov["events_unattributed"]) == (
        6,
        4,
        2,
    )
    assert [(c["caller"], c["event_count"]) for c in cov["unattributed_callers"]] == [
        ("stranger@example.com", 2)
    ]
    owner = next(r for r in cov["rows"] if r["role_name"] == "Owner")
    assert owner["top_operations"] == [
        {"operation_name": "Microsoft.Compute/virtualMachines/write", "count": 3},
        {"operation_name": "Microsoft.Compute/virtualMachines/read", "count": 1},
    ]
    assert owner["observed_op_count"] == 2
    assert owner["coverage_pct"] == 100  # Owner's single "*" pattern is exercised
    newest = datetime.fromisoformat(cov["principal_last_activity"]["sp-1"])
    assert abs((newest - (now - timedelta(days=1))).total_seconds()) < 5


def test_recompute_refreshes_an_existing_coverage_cache(app_with_sqlite: TestClient):
    """Regression: recording principal activity used to ``expire_all()`` the
    session before commit, silently discarding the updated cache payload on
    every recompute after the first."""
    from app.db.models import ActivityEvent, ScanCoverage
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    with SessionLocal() as db:
        assert db.get(ScanCoverage, scan_id).payload["events_considered"] == 0
        db.add(
            ActivityEvent(
                subscription_id=SUB,
                event_data_id="late",
                caller="sp-one",
                caller_type="ServicePrincipal",
                operation_name="Microsoft.Compute/virtualMachines/read",
                resource_id=None,
                status="Succeeded",
                occurred_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        db.commit()
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    with SessionLocal() as db:
        payload = db.get(ScanCoverage, scan_id).payload
    assert (payload["events_considered"], payload["events_attributed"]) == (1, 1)
    assert "sp-1" in payload["principal_last_activity"]


def test_an_empty_group_is_unused_for_certain(app_with_sqlite: TestClient):
    from app.db.models import ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_baseline(db)
        db.add(ScanGroupResolution(scan_id=scan_id, group_id="g-1"))  # read from Graph: no members
        db.commit()

    body = app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    [f] = [
        f
        for f in body["findings"]
        if f["title"] == "Reader → grp-readers on prod" and f["category"] == "unused"
    ]
    assert f["detail"] == "The group has no members in Entra ID, so nobody uses this access."
    assert f["suggestion"].startswith("Remove the grant, or the group")
    assert "membership_unknown" not in {
        x["category"] for x in body["findings"] if x["title"] == f["title"]
    }


def test_a_partners_group_holding_a_powerful_role_is_flagged_not_taken_for_deleted(
    app_with_sqlite: TestClient,
):
    """A group of another tenant (Azure and Entra type it ForeignGroup): its name and members
    can't be read from here. That is no reason to call it deleted or its access unused."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        scan = _seed_scan(db)
        db.add_all(
            [
                _ra(scan.id, SUB_SCOPE, "subscription", "partner-grp", "ForeignGroup", OWNER),
                _ra(scan.id, SUB_SCOPE, "subscription", "partner-readers", "ForeignGroup", READER),
            ]
        )
        db.commit()
        scan_id = scan.id
    cats = _categories(
        app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute").json()
    )
    assert cats["foreign_group"] == ["Owner → partner-grp on prod"]  # powerful roles only
    assert "unresolved_principal" not in cats
    assert "unused" not in cats
