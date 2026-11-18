"""Owners and secrets in the monthly review (core/review_subjects.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.tools.graph_resolver import EntraUser
from tests.test_owners import _owned_world

ADA = EntraUser("ada-id", "Ada", "ada@example.org")
NOV = datetime(2026, 11, 2, 12, tzinfo=UTC)


def _world(client: TestClient) -> int:
    """_owned_world (sp-deploy Owner of prod, owned by ada and by sp-ci), plus an expired
    secret on its registration; ada is responsible for prod."""
    from app.core.findings import compute_scan_findings
    from app.core.responsible import set_responsible
    from app.core.reviews import open_and_sync
    from app.db.models import AppCredential
    from app.db.session import SessionLocal
    from tests.test_registry import SUB_SCOPE

    scan_id = _owned_world(client)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add(
            AppCredential(
                scan_id=scan_id,
                object_id="sp-deploy",
                via="registration",
                kind="secret",
                key_id="old",
                start_at=now - timedelta(days=400),
                end_at=now - timedelta(days=5),
            )
        )
        db.commit()
        compute_scan_findings(db, scan_id)
        set_responsible(db, SUB_SCOPE, ADA, "manual", None)
        db.commit()
        open_and_sync(db, NOV)
    return scan_id


def _items(client: TestClient) -> dict[str, tuple[str | None, dict]]:
    """{subject: (round scope, item)} over every round."""
    out = {}
    for r in client.get("/api/reviews").json():
        for i in client.get(f"/api/reviews/{r['id']}").json()["items"]:
            key = i["principal_id"] if i["kind"] == "grant" else f"{i['kind']}:{i['principal_id']}"
            out[key] = (r["scope_id"], r["id"], i)
    return out


def test_owners_and_secrets_the_findings_flag_join_the_review(app_with_sqlite: TestClient):
    from tests.test_registry import SUB_SCOPE

    _world(app_with_sqlite)
    items = _items(app_with_sqlite)
    # sp-ci owns the app: reviewed by ada, with prod's grants; no suggestion for an owner.
    scope, _rid, ci = items["owner:sp-ci"]
    assert scope == SUB_SCOPE and ci["suggested"] is None
    assert ci["detail"]["object_id"] == "sp-deploy" and ci["role_name"] == "Owner"
    # ada owns it too, but never reviews her own ownership: the admins do.
    scope, _rid, ada = items["owner:ada-id"]
    assert scope is None and ada["moved_from_scope"] == SUB_SCOPE
    # The expired secret: delete it.
    scope, _rid, secret = items["secret:sp-deploy"]
    assert (scope, secret["suggested"], secret["detail"]["key_id"]) == (SUB_SCOPE, "remove", "old")
    assert secret["remove_command"] == "az ad app credential delete --id app-1 --key-id old"


def test_keeping_every_owner_a_finding_lists_marks_it_reviewed(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    items = _items(app_with_sqlite)

    def put(key: str, decision: str | None, note: str | None = None):
        _s, rid, item = items[key]
        return app_with_sqlite.put(
            f"/api/reviews/{rid}/items/{item['id']}", json={"decision": decision, "note": note}
        )

    def status(category: str) -> str:
        found = app_with_sqlite.get("/api/findings", params={"limit": 500}).json()["findings"]
        return next(f["status"] for f in found if f["category"] == category)

    assert put("owner:sp-ci", "change", "x").status_code == 400  # an owner stays or goes
    assert put("owner:sp-ci", "keep").status_code == 200
    assert status("app_owned_by_app") == "reviewed"  # lists only sp-ci
    assert status("app_owners") == "open"
    assert put("owner:ada-id", "keep").status_code == 200
    assert status("app_owners") == "reviewed"
    # Undoing a keep reopens what it marked.
    assert put("owner:sp-ci", None).status_code == 200
    assert status("app_owned_by_app") == "open"
    assert status("app_owners") == "reviewed"  # lists only ada


def test_removing_a_secret_becomes_a_todo_the_next_check_closes(app_with_sqlite: TestClient):
    from app.core.current_view import current_view
    from app.core.todos import close_done
    from app.db.models import AppCredential, Todo
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    items = _items(app_with_sqlite)
    _s, rid, secret = items["secret:sp-deploy"]
    for key, (_scope, r, item) in items.items():
        if r == rid and item["decision"] is None:
            decision = "remove" if key == "secret:sp-deploy" else "keep"
            app_with_sqlite.put(
                f"/api/reviews/{rid}/items/{item['id']}", json={"decision": decision}
            )
    assert app_with_sqlite.post(f"/api/reviews/{rid}/finish").status_code == 200
    with SessionLocal() as db:
        (todo,) = db.query(Todo).filter(Todo.kind == "secret").all()
        assert todo.commands == ["az ad app credential delete --id app-1 --key-id old"]
        assert close_done(db, current_view(db)) == 0  # still there
        db.query(AppCredential).filter_by(key_id="old").delete()
        db.commit()
        assert close_done(db, current_view(db)) == 1
    assert secret
