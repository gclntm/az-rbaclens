"""Entra ID as a place: every page reads its grants right (plan item 1, step 5)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.entra_roles import ENTRA_BUILTIN, ENTRA_PLACE
from app.core.findings import grant_fingerprint
from tests.test_registry import HIERARCHY, MG, OWNER, SUB_SCOPE, _ra, _seed_roles

BUILTIN = json.loads((Path(__file__).parent / "fixtures" / "entra_builtin_roles.json").read_text())
GA = BUILTIN["Global Administrator"]["templateId"].lower()
GREADER = BUILTIN["Global Reader"]["templateId"].lower()
GA_PATH = f"{ENTRA_PLACE}/roleDefinitions/{GA}"
GREADER_PATH = f"{ENTRA_PLACE}/roleDefinitions/{GREADER}"
WITH_ENTRA = {**HIERARCHY, ENTRA_PLACE: {"name": "Entra ID", "level": "directory", "parent": None}}


def _entra(scan_id: int, pid: str, ptype: str, role: str, name: str, **kw):
    from app.db.models import RoleAssignment

    return RoleAssignment(
        scan_id=scan_id,
        scope=ENTRA_PLACE,
        scope_name="Entra ID",
        scope_level="directory",
        principal_id=pid,
        principal_name=name,
        principal_type=ptype,
        role_definition_id=role,
        role_name="Global Administrator" if role == GA_PATH else "Global Reader",
        role_type=ENTRA_BUILTIN,
        assignment_type=kw.pop("assignment_type", "Assigned"),
        raw={"id": f"ra-{pid}", "source": "entra", "directoryScopeId": "/"},
        **kw,
    )


def _world(client: TestClient) -> tuple[int, int]:
    """Two checks of the root. A: ada Global Administrator, grp-readers Global Reader
    (members: alice). B: carol becomes Global Administrator too; bob joins grp-readers;
    sp-1 stays Owner on the subscription."""
    from app.db.models import GroupMembership, RoleDefinition, Scan, ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        for rid, name in ((GA, "Global Administrator"), (GREADER, "Global Reader")):
            db.add(
                RoleDefinition(
                    role_definition_id=rid,
                    role_name=name,
                    role_type=ENTRA_BUILTIN,
                    permissions=[{"allowedResourceActions": BUILTIN[name]["actions"]}],
                )
            )
        ids = []
        for offset, extra in ((-3600, []), (0, ["carol", "bob"])):
            now = datetime.now(UTC) + timedelta(seconds=offset)
            scan = Scan(
                scope=MG,
                scope_level="mgmt_group",
                status="done",
                created_at=now,
                finished_at=now,
                hierarchy=WITH_ENTRA,
                eligibility_status="read",
            )
            db.add(scan)
            db.flush()
            rows = [
                _ra(
                    scan.id, SUB_SCOPE, "subscription", "sp-1", "ServicePrincipal", OWNER, "sp-one"
                ),
                _entra(scan.id, "ada-id", "User", GA_PATH, "ada@example.org"),
                _entra(scan.id, "grp-readers", "Group", GREADER_PATH, "grp-readers"),
                ScanGroupResolution(scan_id=scan.id, group_id="grp-readers"),
                GroupMembership(
                    scan_id=scan.id,
                    group_id="grp-readers",
                    member_id="alice-id",
                    member_type="User",
                    member_name="alice@example.org",
                ),
            ]
            if "carol" in extra:
                rows.append(_entra(scan.id, "carol-id", "User", GA_PATH, "carol@example.org"))
            if "bob" in extra:
                rows.append(
                    GroupMembership(
                        scan_id=scan.id,
                        group_id="grp-readers",
                        member_id="bob-id",
                        member_type="User",
                        member_name="bob@example.org",
                    )
                )
            db.add_all(rows)
            ids.append(scan.id)
        db.commit()
    assert client.post("/api/settings/registry/rebuild").status_code == 200
    from app.core.changes import rebuild_changes
    from app.core.findings import compute_scan_findings

    with SessionLocal() as db:
        for sid in ids:
            compute_scan_findings(db, sid)
        rebuild_changes(db)
    return ids[0], ids[1]


def test_entra_id_is_a_place_of_its_own_with_its_levels(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    tree = app_with_sqlite.get("/api/scopes/tree").json()
    roots = {n["id"]: n for n in tree}
    assert ENTRA_PLACE in roots and roots[ENTRA_PLACE]["level"] == "directory"
    assert roots[ENTRA_PLACE]["latest_scan"] is not None  # the check that read it owns it
    assert MG in roots  # Entra sits beside the Azure tree, above nothing

    caps = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": ENTRA_PLACE}).json()
    levels = {lvl["level"]: lvl for lvl in caps["levels"]}
    who = {i["id"] for i in levels["access"]["identities"]}
    assert who == {"ada-id", "carol-id"}
    readers = {i["id"] for i in levels["keys"]["identities"]}
    assert "grp-readers" in readers  # Global Reader reads BitLocker keys

    # Entra roles never flow into the Azure places below.
    sub = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": SUB_SCOPE}).json()
    everyone = {i["id"] for lvl in sub["levels"] for i in lvl["identities"]}
    assert everyone == {"sp-1"}


def test_the_changes_feed_says_who_got_an_entra_role(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    items = app_with_sqlite.get("/api/changes").json()["items"]
    got = {(i["kind"], i["principal_id"], i["scope"]) for i in items}
    assert ("granted", "carol-id", ENTRA_PLACE) in got
    assert ("member_added", "bob-id", ENTRA_PLACE) in got
    place = app_with_sqlite.get("/api/changes", params={"scope": ENTRA_PLACE}).json()["items"]
    assert {i["principal_id"] for i in place} == {"carol-id", "bob-id"}


def test_usage_findings_never_judge_entra_roles(app_with_sqlite: TestClient):
    """The Activity Log doesn't record Entra actions: "unused" can't be said of them."""
    _world(app_with_sqlite)
    findings = app_with_sqlite.get("/api/findings", params={"limit": 500}).json()
    on_entra = [f for f in findings["findings"] if f.get("scope") == ENTRA_PLACE]
    assert not [f for f in on_entra if f["category"] in ("unused", "over_privileged")]


def _get_routes(client: TestClient) -> list[str]:
    return sorted(
        {
            r.path
            for r in client.app.routes
            if "GET" in getattr(r, "methods", set()) and r.path.startswith("/api/")
        }
    )


def test_every_page_answers_with_entra_grants_on_file(app_with_sqlite: TestClient):
    _, b = _world(app_with_sqlite)
    grant = grant_fingerprint(ENTRA_PLACE, GA_PATH, "ada-id")
    values = {
        "scope_id": ENTRA_PLACE.lstrip("/"),
        "principal_id": "ada-id",
        "grant_id": grant,
        "role_id": GA_PATH.lstrip("/"),
        "scan_id": str(b),
    }
    skipped, failed = [], []
    for path in _get_routes(app_with_sqlite):
        params = re.findall(r"{(\w+)(?::path)?}", path)
        if any(p not in values for p in params):
            skipped.append(path)
            continue
        url = re.sub(r"{(\w+)(?::path)?}", lambda m: values[m.group(1)], path)
        query = (
            {"scope": ENTRA_PLACE}
            if "scope" in path
            or path.endswith(("assignments", "capabilities", "history", "activity"))
            else {}
        )
        status = app_with_sqlite.get(url, params=query).status_code
        if status >= 500:
            failed.append(f"{url} → {status}")
    assert failed == [], failed


# ---- who gave an Entra role (plan item 1, step 2) ---------------------------------------


def _core_entry(
    audit_id: str, activity: str, member: str, role: str, by: dict, minutes_ago: int
) -> dict:
    """A Core Directory role entry: the member first, the role in its modified properties."""
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return {
        "id": audit_id,
        "category": "RoleManagement",
        "activityDisplayName": activity,
        "activityDateTime": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "result": "success",
        "loggedByService": "Core Directory",
        "initiatedBy": by,
        "targetResources": [
            {
                "id": member.upper(),
                "type": "User",
                "userPrincipalName": f"{member}@example.org",
                "modifiedProperties": [
                    {"displayName": "Role.TemplateId", "oldValue": None, "newValue": f'"{role}"'},
                    {
                        "displayName": "Role.DisplayName",
                        "oldValue": None,
                        "newValue": '"Global Administrator"',
                    },
                ],
            },
            {"id": "role-object-id", "type": "Role"},
        ],
    }


def _pim_entry(
    audit_id: str, activity: str, member: str, role: str, by: dict, minutes_ago: int
) -> dict:
    """A PIM entry: the role first (TemplateId), then directory, the member, the request."""
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return {
        "id": audit_id,
        "category": "RoleManagement",
        "activityDisplayName": activity,
        "activityDateTime": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "result": "success",
        "loggedByService": "PIM",
        "initiatedBy": by,
        "targetResources": [
            {
                "id": role,
                "type": "Role",
                "modifiedProperties": [
                    {"displayName": "TemplateId", "oldValue": None, "newValue": f'"{role}"'}
                ],
            },
            {"id": "dir", "type": "Directory"},
            {"id": member.upper(), "type": "User"},
            {"id": "req-1", "type": "Request"},
        ],
    }


PERSON = {"user": {"id": "ADMIN-ID", "userPrincipalName": "admin@example.org"}, "app": None}
PIM_APP = {"user": None, "app": {"servicePrincipalId": "pim-sp", "displayName": "MS-PIM"}}


def test_role_entries_are_read_in_both_shapes():
    from app.tools import entra_audit as ea

    core = ea.parse(_core_entry("a", "Add member to role", "carol-id", GA, PERSON, 5))
    assert (core.kind, core.action, core.role_id, core.member_id, core.group_id) == (
        "role",
        "add",
        GA,
        "carol-id",
        None,
    )
    pim = ea.parse(
        _pim_entry(
            "b", "Add member to role in PIM completed (permanent)", "carol-id", GA, PERSON, 5
        )
    )
    assert (pim.kind, pim.role_id, pim.member_id, pim.initiator_name) == (
        "role",
        GA,
        "carol-id",
        "admin@example.org",
    )
    eligible = ea.parse(
        _pim_entry(
            "c",
            "Remove eligible member from role in PIM completed (permanent)",
            "bob-id",
            GA,
            PERSON,
            5,
        )
    )
    assert (eligible.kind, eligible.action) == ("eligible", "remove")
    activation = ea.parse(
        _pim_entry("d", "Add member to role completed (PIM activation)", "ada-id", GA, PIM_APP, 5)
    )
    assert activation.kind == "activation"
    assert ea.parse({**_core_entry("e", "Update role", "x", GA, PERSON, 5)}) is None


def test_a_new_entra_role_gets_the_person_who_gave_it_over_the_pim_service(
    app_with_sqlite: TestClient,
):
    from app.core.changes import rebuild_changes, refine_from_directory_audits
    from app.db.models import AccessChange
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea

    _world(app_with_sqlite)
    with SessionLocal() as db:
        # PIM records the person; the directory records the PIM service, a minute later.
        db.add(
            ea.parse(
                _pim_entry(
                    "p",
                    "Add member to role in PIM completed (permanent)",
                    "carol-id",
                    GA,
                    PERSON,
                    12,
                )
            )
        )
        db.add(ea.parse(_core_entry("c", "Add member to role", "carol-id", GA, PIM_APP, 11)))
        db.commit()
        assert refine_from_directory_audits(db) == 1  # the entries came after the check
        carol = db.query(AccessChange).filter_by(kind="granted", principal_id="carol-id").one()
        assert (carol.source, carol.actor_name, carol.actor_type) == (
            "entra_audit",
            "admin@example.org",
            "user",
        )
        rebuild_changes(db)  # matched at check time too, from the stored entries
        carol = db.query(AccessChange).filter_by(kind="granted", principal_id="carol-id").one()
        assert carol.source == "entra_audit"


def test_a_pim_activation_seen_by_a_check_is_not_a_change(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.models import AccessChange, Scan
    from app.db.session import SessionLocal

    _, b = _world(app_with_sqlite)
    with SessionLocal() as db:
        now = datetime.now(UTC) + timedelta(seconds=60)
        scan = Scan(
            scope=MG,
            scope_level="mgmt_group",
            status="done",
            created_at=now,
            finished_at=now,
            hierarchy=WITH_ENTRA,
            eligibility_status="read",
        )
        db.add(scan)
        db.flush()
        prev = db.get(Scan, b)
        db.add_all(
            [
                _ra(
                    scan.id, SUB_SCOPE, "subscription", "sp-1", "ServicePrincipal", OWNER, "sp-one"
                ),
                _entra(scan.id, "ada-id", "User", GA_PATH, "ada@example.org"),
                _entra(scan.id, "carol-id", "User", GA_PATH, "carol@example.org"),
                _entra(scan.id, "grp-readers", "Group", GREADER_PATH, "grp-readers"),
                # dave activated Global Administrator through PIM while the check ran.
                _entra(
                    scan.id,
                    "dave-id",
                    "User",
                    GA_PATH,
                    "dave@example.org",
                    assignment_type="Activated",
                ),
            ]
        )
        db.commit()
        assert prev is not None
        record_changes_for_scan(db, scan.id)
        assert db.query(AccessChange).filter_by(principal_id="dave-id").count() == 0


def test_the_identity_page_lists_its_pim_activations_of_entra_roles(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add(
            ea.parse(
                _pim_entry(
                    "act",
                    "Add member to role completed (PIM activation)",
                    "ada-id",
                    GA,
                    PIM_APP,
                    30,
                )
            )
        )
        db.commit()
    page = app_with_sqlite.get("/api/principals/ada-id").json()
    assert [(a["role_id"], a["role_name"]) for a in page["entra_activations"]] == [
        (GA, "Global Administrator")
    ]
    assert app_with_sqlite.get("/api/principals/carol-id").json()["entra_activations"] == []


# ---- needs a look (plan item 1, step 4) ---------------------------------------------------


def _entra_findings(client: TestClient) -> dict[tuple[str, str], dict]:
    found = client.get("/api/findings", params={"limit": 500}).json()["findings"]
    return {(f["category"], f["principal_id"]): f for f in found if f["scope"] == ENTRA_PLACE}


def test_entra_roles_get_their_own_findings(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    got = _entra_findings(app_with_sqlite)
    assert set(got) == {
        ("entra_standing_admin", "ada-id"),
        ("entra_standing_admin", "carol-id"),
        ("entra_role_via_group", "grp-readers"),  # Global Reader reads BitLocker keys
    }
    assert got[("entra_standing_admin", "ada-id")]["severity"] == "high"


def test_break_glass_apps_and_too_many_global_admins(app_with_sqlite: TestClient):
    from app.core.findings import compute_scan_findings
    from app.db.session import SessionLocal

    _, b = _world(app_with_sqlite)
    assert (
        app_with_sqlite.put("/api/settings/advice", json={"break_glass": ["ADA-ID"]}).status_code
        == 200
    )
    with SessionLocal() as db:
        db.add_all(
            [_entra(b, f"admin-{i}", "User", GA_PATH, f"admin{i}@example.org") for i in range(2)]
            + [_entra(b, "sp-ga", "ServicePrincipal", GA_PATH, "sp-automation")]
        )
        db.commit()
        compute_scan_findings(db, b)
    got = _entra_findings(app_with_sqlite)
    assert ("entra_standing_admin", "ada-id") not in got  # break-glass: meant to be standing
    assert ("entra_app_privileged", "sp-ga") in got
    assert ("entra_standing_admin", "sp-ga") not in got  # an app can't use PIM
    many = got[("entra_too_many_global_admins", "carol-id")]
    assert many["evidence"]["holders"] == 5  # ada, carol, two admins, the app


def test_entra_id_gets_a_responsible_person_and_a_monthly_review(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    tree = app_with_sqlite.get("/api/responsible/tree").json()
    ids = set()

    def walk(nodes):
        for n in nodes:
            ids.add(n["scope_id"])
            walk(n.get("children", []))

    walk(tree if isinstance(tree, list) else tree.get("nodes", []))
    assert ENTRA_PLACE in ids


# ---- the first read, and a whole check --------------------------------------------------


def test_the_first_check_that_reads_entra_reports_nothing_as_new(app_with_sqlite: TestClient):
    """After an upgrade the previous check never read Entra ID: what was always there is not news."""
    from app.core.changes import record_changes_for_scan
    from app.db.models import AccessChange, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        ids = []
        for offset, hierarchy in ((-3600, HIERARCHY), (0, WITH_ENTRA)):
            now = datetime.now(UTC) + timedelta(seconds=offset)
            scan = Scan(
                scope=MG,
                scope_level="mgmt_group",
                status="done",
                created_at=now,
                finished_at=now,
                hierarchy=hierarchy,
                eligibility_status="read",
            )
            db.add(scan)
            db.flush()
            db.add(
                _ra(scan.id, SUB_SCOPE, "subscription", "sp-1", "ServicePrincipal", OWNER, "sp-one")
            )
            if ENTRA_PLACE in hierarchy:
                db.add(_entra(scan.id, "ada-id", "User", GA_PATH, "ada@example.org"))
            ids.append(scan.id)
        db.commit()
        record_changes_for_scan(db, ids[1])
        assert db.query(AccessChange).filter_by(scope=ENTRA_PLACE).count() == 0


class _Token:
    def __init__(self, tid: str):
        import base64

        claims = base64.urlsafe_b64encode(json.dumps({"tid": tid}).encode()).decode().rstrip("=")
        self.token = f"h.{claims}.s"


class _Credential:
    def __init__(self, tid: str):
        self.tid = tid

    def get_token(self, *_scopes):
        return _Token(self.tid)


def _run_root_check(app_with_sqlite: TestClient, monkeypatch, scope: str, entra) -> int:
    """Run a whole check of ``scope`` with Azure and Graph faked; ``entra`` is what Graph gives."""
    from app.tools import entra_directory as ed
    from app.tools import iam_access as tool
    from app.tools import pim

    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": scope}).json()["id"]
    root = f"/providers/microsoft.management/managementgroups/{scope}"
    monkeypatch.setattr(tool, "get_credential", lambda: _Credential("tenant-1"))
    monkeypatch.setattr(
        tool,
        "collect_assignments",
        lambda *a: (
            [],
            {root: {"name": "Tenant Root Group", "level": "mgmt_group", "parent": None}},
            {},
            {},
        ),
    )
    monkeypatch.setattr(pim, "collect_pim", lambda *a, **k: pim.PimResult(status="read"))

    def collect(_credential):
        if isinstance(entra, Exception):
            raise entra
        return entra

    monkeypatch.setattr(ed, "collect", collect)
    from app.tools import graph_owners

    # Owners are read by their own tests (test_owners.py): no Graph call here.
    monkeypatch.setattr(graph_owners, "collect", lambda *a, **k: graph_owners.Owners())
    resolved: list[list[str]] = []
    monkeypatch.setattr(
        tool,
        "resolve_group_memberships",
        # Graph refuses the members of role-assignable groups to a Directory Reader: None.
        lambda ids, _c, strict=False: resolved.append(ids) or dict.fromkeys(ids),
    )
    tool.run_scan(scan_id)
    return scan_id


def test_a_check_of_the_tenant_root_group_stores_entra_roles(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.db.models import RoleAssignment, RoleDefinition, RoleEligibility, Scan
    from app.db.session import SessionLocal
    from app.tools import entra_directory as ed

    roles = ed.EntraRoles(
        assignments=[
            _entra(0, "ada-id", "User", GA_PATH, "ada@example.org"),
            _entra(0, "grp-admins", "Group", GA_PATH, "grp-admins"),
        ],
        eligibilities=[
            RoleEligibility(
                azure_id="el-1",
                scope=ENTRA_PLACE,
                scope_name="Entra ID",
                scope_level="directory",
                principal_id="bob-id",
                principal_type="User",
                role_definition_id=GA_PATH,
                role_name="Global Administrator",
            )
        ],
        role_definitions={
            GA: {
                "name": "Global Administrator",
                "role_type": ENTRA_BUILTIN,
                "permissions": [
                    {"allowedResourceActions": BUILTIN["Global Administrator"]["actions"]}
                ],
            }
        },
        group_ids={"grp-admins"},
    )
    for ra in roles.assignments:
        ra.scan_id = None
    scan_id = _run_root_check(app_with_sqlite, monkeypatch, "tenant-1", roles)
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        assert scan.status == "done" and scan.hierarchy[ENTRA_PLACE]["level"] == "directory"
        assert db.query(RoleAssignment).filter_by(scan_id=scan_id, scope=ENTRA_PLACE).count() == 2
        assert db.query(RoleEligibility).filter_by(scan_id=scan_id, scope=ENTRA_PLACE).count() == 1
        assert db.get(RoleDefinition, GA).role_type == ENTRA_BUILTIN
    tree = {n["id"] for n in app_with_sqlite.get("/api/scopes/tree").json()}
    assert ENTRA_PLACE in tree


def test_a_refusal_leaves_entra_unread_and_other_roots_never_read_it(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import entra_directory as ed

    refused = _run_root_check(
        app_with_sqlite, monkeypatch, "tenant-1", ed.EntraNotReadable("HTTP 403")
    )
    other = _run_root_check(app_with_sqlite, monkeypatch, "mg-other", AssertionError("not called"))
    with SessionLocal() as db:
        scan = db.get(Scan, refused)
        assert scan.status == "done" and scan.hierarchy[ENTRA_PLACE]["unread"] is True
        other_scan = db.get(Scan, other)
        assert other_scan.status == "done" and ENTRA_PLACE not in other_scan.hierarchy


def test_the_directorys_record_of_a_pim_activation_never_explains_a_grant(
    app_with_sqlite: TestClient,
):
    """carol got Global Administrator; earlier she had also activated it through PIM, which the
    directory records as "Add member to role" by MS-PIM. That entry is not who gave her the role."""
    from app.core.changes import refine_from_directory_audits
    from app.db.models import AccessChange
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add(
            ea.parse(
                _pim_entry(
                    "act",
                    "Add member to role completed (PIM activation)",
                    "carol-id",
                    GA,
                    PIM_APP,
                    30,
                )
            )
        )
        db.add(ea.parse(_core_entry("core", "Add member to role", "carol-id", GA, PIM_APP, 29)))
        db.commit()
        assert refine_from_directory_audits(db) == 0
        carol = db.query(AccessChange).filter_by(kind="granted", principal_id="carol-id").one()
        assert carol.source != "entra_audit"


# ---- reviews and the report ---------------------------------------------------------------


def test_entra_roles_are_reviewed_and_removed_through_graph(app_with_sqlite: TestClient):
    from app.core.reviews import open_and_sync
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    with SessionLocal() as db:
        open_and_sync(db, datetime.now(UTC))
    rounds = app_with_sqlite.get("/api/reviews").json()
    items = [
        i
        for r in rounds
        for i in app_with_sqlite.get(f"/api/reviews/{r['id']}").json()["items"]
        if i["scope"] == ENTRA_PLACE
    ]
    ada = next(i for i in items if i["principal_id"] == "ada-id")
    rid = next(
        r["id"]
        for r in rounds
        if any(
            i["id"] == ada["id"]
            for i in app_with_sqlite.get(f"/api/reviews/{r['id']}").json()["items"]
        )
    )
    removed = app_with_sqlite.put(
        f"/api/reviews/{rid}/items/{ada['id']}", json={"decision": "remove"}
    ).json()
    assert removed["remove_command"] == (
        "az rest --method DELETE --url "
        "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments/ra-ada-id"
    )


def test_the_report_counts_standing_entra_admins_as_privileged(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    report = app_with_sqlite.get("/api/report").json()
    on_entra = {
        (g["principal_id"], g["level"]) for g in report["privileged"] if g["scope"] == ENTRA_PLACE
    }
    assert ("ada-id", "access") in on_entra and ("carol-id", "access") in on_entra


def test_a_powerful_entra_role_held_from_another_tenant_needs_a_look(app_with_sqlite: TestClient):
    from app.core.findings import compute_scan_findings
    from app.db.session import SessionLocal

    _, b = _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add(_entra(b, "partner-grp", "ForeignGroup", GREADER_PATH, "partner-admins"))
        db.commit()
        compute_scan_findings(db, b)
    got = _entra_findings(app_with_sqlite)
    assert got[("foreign_group", "partner-grp")]["severity"] == "high"
    assert ("entra_role_via_group", "partner-grp") not in got


# ---- Entra role changes straight from the audit log (0.34.1) --------------------------------


def _entra_changes(db) -> list[tuple[str, str, str | None, str]]:
    from app.db.models import AccessChange

    return sorted(
        (c.kind, c.principal_id, c.actor_name, c.source)
        for c in db.query(AccessChange).filter_by(scope=ENTRA_PLACE).all()
    )


def test_role_changes_no_check_saw_come_from_the_audit_log(app_with_sqlite: TestClient):
    from app.core.changes import rebuild_changes, record_entra_audit_changes
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea

    _world(app_with_sqlite)
    with SessionLocal() as db:
        # dave got Global Administrator and lost it again between the two checks;
        # PIM and the directory both recorded the grant: one change, by the person.
        db.add(
            ea.parse(
                _pim_entry(
                    "d1",
                    "Add member to role in PIM completed (permanent)",
                    "dave-id",
                    GA,
                    PERSON,
                    40,
                )
            )
        )
        db.add(ea.parse(_core_entry("d2", "Add member to role", "dave-id", GA, PIM_APP, 40)))
        db.add(ea.parse(_core_entry("d3", "Remove member from role", "dave-id", GA, PERSON, 20)))
        # frank lost Global Reader days before RBAC Lens first read Entra ID.
        db.add(
            ea.parse(
                _core_entry(
                    "f1", "Remove member from role", "frank-id", GREADER, PERSON, 3 * 24 * 60
                )
            )
        )
        # ada activated Global Administrator through PIM: use, not a change.
        db.add(
            ea.parse(
                _pim_entry(
                    "a1", "Add member to role completed (PIM activation)", "ada-id", GA, PIM_APP, 30
                )
            )
        )
        db.add(ea.parse(_core_entry("a2", "Add member to role", "ada-id", GA, PIM_APP, 30)))
        # carol's grant, already reported by check B, attributed by the same entries.
        db.add(
            ea.parse(
                _pim_entry(
                    "c1",
                    "Add member to role in PIM completed (permanent)",
                    "carol-id",
                    GA,
                    PERSON,
                    12,
                )
            )
        )
        db.commit()

        # dave given and removed, frank removed; carol's grant is in the feed already.
        assert record_entra_audit_changes(db) == 3
        got = _entra_changes(db)
        assert ("granted", "dave-id", "admin@example.org", "entra_audit") in got
        assert ("removed", "dave-id", "admin@example.org", "entra_audit") in got
        assert ("removed", "frank-id", "admin@example.org", "entra_audit") in got
        assert not [c for c in got if c[1] == "ada-id"]
        assert len([c for c in got if c[1] == "carol-id" and c[0] == "granted"]) == 1
        assert record_entra_audit_changes(db) == 0  # once

        rebuild_changes(db)  # the feed rebuilt from the checks keeps them
        assert ("removed", "frank-id", "admin@example.org", "entra_audit") in _entra_changes(db)
        assert len([c for c in _entra_changes(db) if c[1] == "carol-id" and c[0] == "granted"]) == 1


def test_a_change_from_the_audit_log_is_not_reported_again_by_the_next_check(
    app_with_sqlite: TestClient,
):
    from app.core.changes import record_changes_for_scan, record_entra_audit_changes
    from app.db.models import AccessChange, Scan
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add(ea.parse(_core_entry("e1", "Add member to role", "erin-id", GA, PERSON, -1)))
        db.commit()
        assert record_entra_audit_changes(db) == 1  # the hourly collection, before the check
        now = datetime.now(UTC) + timedelta(minutes=5)
        scan = Scan(
            scope=MG,
            scope_level="mgmt_group",
            status="done",
            created_at=now,
            finished_at=now,
            hierarchy=WITH_ENTRA,
            eligibility_status="read",
        )
        db.add(scan)
        db.flush()
        db.add_all(
            [
                _ra(
                    scan.id, SUB_SCOPE, "subscription", "sp-1", "ServicePrincipal", OWNER, "sp-one"
                ),
                _entra(scan.id, "ada-id", "User", GA_PATH, "ada@example.org"),
                _entra(scan.id, "carol-id", "User", GA_PATH, "carol@example.org"),
                _entra(scan.id, "grp-readers", "Group", GREADER_PATH, "grp-readers"),
                _entra(scan.id, "erin-id", "User", GA_PATH, "erin@example.org"),
            ]
        )
        db.commit()
        record_changes_for_scan(db, scan.id)
        erin = db.query(AccessChange).filter_by(principal_id="erin-id", kind="granted").all()
        assert [(c.source, c.actor_name) for c in erin] == [("entra_audit", "admin@example.org")]


def test_each_collection_fills_in_entra_changes_from_every_entry_on_file(
    app_with_sqlite: TestClient, monkeypatch
):
    """After an upgrade the entries are already stored: the next hourly collection,
    whose own window is empty, adds the changes they hold."""
    from app.db.models import AccessChange
    from app.db.session import SessionLocal
    from app.tools import entra_audit as ea
    from tests.test_entra_audit import _Answer, _Graph, _run

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.add(
            ea.parse(
                _core_entry(
                    "old", "Remove member from role", "frank-id", GREADER, PERSON, 3 * 24 * 60
                )
            )
        )
        db.commit()
    _run(monkeypatch, _Graph(_Answer(200, {"value": []})))
    with SessionLocal() as db:
        frank = db.query(AccessChange).filter_by(principal_id="frank-id").one()
        assert (frank.kind, frank.source, frank.actor_name) == (
            "removed",
            "entra_audit",
            "admin@example.org",
        )
