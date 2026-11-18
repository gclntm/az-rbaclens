"""Owners added or removed, on the Changes page (core/owner_changes.py, plan "owners")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.tools import entra_audit as ea
from tests.test_changes import _two_checks


def _owner_entry(
    audit_id: str,
    *,
    action: str = "Add owner to application",
    owner: str = "eve-id",
    key: str = "Application.AppId",
    value: str = "app-1",
    when: datetime | None = None,
) -> dict:
    """An owner entry as Graph returns it: the owner first, naming the object in its
    properties; the object second."""
    prop = {"displayName": key, "oldValue": None, "newValue": f'"{value}"'}
    if action.startswith("Remove"):
        prop = {**prop, "oldValue": prop["newValue"], "newValue": None}
    return {
        "id": audit_id,
        "category": "ApplicationManagement",
        "activityDisplayName": action,
        "activityDateTime": (when or datetime.now(UTC) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "result": "success",
        "initiatedBy": {"user": {"id": "admin-id", "userPrincipalName": "admin@example.org"}},
        "targetResources": [
            {
                "id": owner,
                "type": "User",
                "userPrincipalName": "eve@example.org",
                "modifiedProperties": [prop],
            },
            {"id": "reg-object-1", "type": "Application", "displayName": "deploy"},
        ],
    }


def test_owner_entries_name_the_owner_and_what_it_owns():
    app = ea.parse(_owner_entry("a"))
    assert (app.kind, app.action, app.member_id, app.group_id) == (
        "owner",
        "add",
        "eve-id",
        "appid:app-1",  # a registration: by its client id
    )
    sp = ea.parse(
        _owner_entry(
            "b",
            action="Remove owner from service principal",
            key="ServicePrincipal.ObjectID",
            value="SP-1",
        )
    )
    assert (sp.action, sp.group_id) == ("remove", "sp-1")
    group = ea.parse(
        _owner_entry("c", action="Add owner to group", key="Group.ObjectID", value="g")
    )
    assert group.group_id == "g"
    assert ea.parse(_owner_entry("d", key="Something.Else")) is None  # can't tell what it owns


def _owned(db, scan_id: int, *owners: str, object_id: str = "sp-1", app_id: str | None = "app-1"):
    from app.db.models import PrincipalOwner, ScanOwnerResolution

    db.add(ScanOwnerResolution(scan_id=scan_id, object_id=object_id, app_id=app_id))
    for o in owners:
        db.add(
            PrincipalOwner(
                scan_id=scan_id,
                object_id=object_id,
                object_kind="app",
                via="registration",
                owner_id=o,
                owner_type="User",
                owner_name=f"{o.removesuffix('-id')}@example.org",
            )
        )


def _owner_changes(db) -> list:
    from app.db.models import AccessChange

    return (
        db.query(AccessChange)
        .filter(AccessChange.kind.in_(("owner_added", "owner_removed")))
        .order_by(AccessChange.occurred_at)
        .all()
    )


def test_an_owner_added_between_two_checks_is_a_change_with_its_author(
    app_with_sqlite: TestClient,
):
    from app.core.changes import record_changes_for_scan, refine_from_directory_audits
    from app.db.session import SessionLocal

    a, b = _two_checks(app_with_sqlite)  # sp-1 is Owner of the management group in both
    with SessionLocal() as db:
        _owned(db, a, "ada-id")
        _owned(db, b, "ada-id", "eve-id")
        # grp-1's owners were read only by the second check: unknown before, not new.
        _owned(db, b, "carol-id", object_id="grp-1", app_id=None)
        db.commit()
        record_changes_for_scan(db, b)
        [c] = _owner_changes(db)
        assert (c.kind, c.principal_id, c.via_group_id, c.via_group_name, c.level, c.source) == (
            "owner_added",
            "eve-id",
            "sp-1",
            "sp-one",
            "access",
            "daily_check",
        )
        # The audit log arrives later: the registration's entry gives author and time.
        db.add(ea.parse(_owner_entry("e1")))
        db.commit()
        refine_from_directory_audits(db)
        [c] = _owner_changes(db)
        assert (c.source, c.actor_name) == ("entra_audit", "admin@example.org")


def test_an_owner_added_and_removed_between_checks_comes_from_the_audit_log(
    app_with_sqlite: TestClient,
):
    from app.core.owner_changes import record_owner_audit_changes
    from app.db.session import SessionLocal

    a, b = _two_checks(app_with_sqlite)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        _owned(db, a, "ada-id")
        _owned(db, b, "ada-id")
        for row in (
            # Added to the registration and the service principal at once: one change.
            _owner_entry("r1", when=now - timedelta(minutes=50)),
            _owner_entry(
                "r2",
                action="Add owner to service principal",
                key="ServicePrincipal.ObjectID",
                value="sp-1",
                when=now - timedelta(minutes=49),
            ),
            _owner_entry(
                "r3", action="Remove owner from application", when=now - timedelta(minutes=20)
            ),
            # An app that holds no access: not on the Changes page.
            _owner_entry("r4", value="app-without-access", when=now - timedelta(minutes=20)),
        ):
            db.add(ea.parse(row))
        db.commit()
        assert record_owner_audit_changes(db) == 2
        got = _owner_changes(db)
        assert [(c.kind, c.principal_id, c.via_group_id, c.source) for c in got] == [
            ("owner_added", "eve-id", "sp-1", "entra_audit"),
            ("owner_removed", "eve-id", "sp-1", "entra_audit"),
        ]
        assert record_owner_audit_changes(db) == 0  # each hour: only what is missing


def test_the_changes_page_lists_owner_changes_as_given_and_removed(app_with_sqlite: TestClient):
    from app.core.owner_changes import record_owner_audit_changes
    from app.db.session import SessionLocal

    _a, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _owned(db, b, "ada-id")
        db.add(ea.parse(_owner_entry("x1")))
        db.commit()
        record_owner_audit_changes(db)
    body = app_with_sqlite.get("/api/changes").json()
    owners = [i for i in body["items"] if i["kind"] == "owner_added"]
    assert [(i["principal_id"], i["via_group_id"]) for i in owners] == [("eve-id", "sp-1")]
