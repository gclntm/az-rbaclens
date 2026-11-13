"""Phase 12: advice in reviews (suggested, accept all, change) and to-dos closed by the daily check."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func

from tests.test_registry import SUB_SCOPE
from tests.test_reviews import NOV, _detail, _open, _rounds, _world


def _admins_round(client: TestClient) -> tuple[int, dict]:
    rid = _rounds(client)[None]["id"]
    [item] = _detail(client, rid)["items"]  # sp-1 Owner on the MG
    return rid, item


def test_every_undecided_item_carries_the_advice(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    _, sp = _admins_round(app_with_sqlite)
    assert sp["suggested_rule"] == "R09 app-owner"  # an app that can change access, high up
    assert (sp["suggested"], sp["decision"]) == ("change", None)
    marc = _detail(app_with_sqlite, _rounds(app_with_sqlite)[SUB_SCOPE]["id"])["items"]
    assert {i["suggested"] for i in marc} == {"keep"}  # two Reader grants: nothing to do


def test_change_needs_a_note(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    rid, sp = _admins_round(app_with_sqlite)
    url = f"/api/reviews/{rid}/items/{sp['id']}"
    bad = app_with_sqlite.put(url, json={"decision": "change", "note": "  "})
    assert bad.status_code == 400 and "say what to change" in bad.json()["detail"]
    ok = app_with_sqlite.put(
        url, json={"decision": "change", "note": "Give it Contributor instead"}
    ).json()
    assert ok["decision"] == "change"
    assert _rounds(app_with_sqlite)[None]["progress"]["change"] == 1


def test_accept_all_advice_confirms_only_undecided_items(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    rid = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    items = _detail(app_with_sqlite, rid)["items"]
    app_with_sqlite.put(f"/api/reviews/{rid}/items/{items[0]['id']}", json={"decision": "remove"})

    counts = app_with_sqlite.post(f"/api/reviews/{rid}/accept-advice").json()
    assert counts == {"keep": 1, "remove": 0, "change": 0, "ask": 0}
    after = {i["id"]: i["decision"] for i in _detail(app_with_sqlite, rid)["items"]}
    assert after[items[0]["id"]] == "remove"  # a decision already made is left alone
    assert (
        app_with_sqlite.post(f"/api/reviews/{rid}/accept-advice").json()["keep"] == 0
    )  # nothing left


def test_accepted_change_keeps_the_advice_as_its_note(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    rid, _ = _admins_round(app_with_sqlite)
    assert app_with_sqlite.post(f"/api/reviews/{rid}/accept-advice").json()["change"] == 1
    [item] = _detail(app_with_sqlite, rid)["items"]
    assert item["note"].startswith("As advised (R09 app-owner)")


def test_finishing_turns_remove_and_change_into_todos_closed_by_the_next_check(
    app_with_sqlite: TestClient,
):
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    _open(NOV)
    rid, sp = _admins_round(app_with_sqlite)
    app_with_sqlite.put(f"/api/reviews/{rid}/items/{sp['id']}", json={"decision": "remove"})
    assert app_with_sqlite.post(f"/api/reviews/{rid}/finish").status_code == 200

    [todo] = app_with_sqlite.get("/api/todos").json()
    assert (todo["action"], todo["status"], todo["overdue"], todo["period"]) == (
        "remove",
        "open",
        False,
        "2026-11",
    )
    assert todo["commands"][0].startswith("az role assignment delete")
    assert todo["can_dismiss"] is True
    assert app_with_sqlite.get("/api/todos/summary").json() == {
        "open": 1,
        "overdue": 0,
        "mine_open": 1,
        "mine_overdue": 0,
    }
    script = app_with_sqlite.get("/api/todos/script").text
    assert script.startswith("#!/bin/sh") and "az role assignment delete" in script
    assert app_with_sqlite.post(f"/api/reviews/{rid}/finish").status_code == 403  # once
    assert len(app_with_sqlite.get("/api/todos").json()) == 1

    # Someone runs the command; the next check no longer sees the grant.
    with SessionLocal() as db:
        db.query(RoleAssignment).filter(func.lower(RoleAssignment.principal_id) == "sp-1").delete(
            synchronize_session=False
        )
        db.commit()
    assert _open(NOV + timedelta(days=1))["todos_done"] == 1
    assert app_with_sqlite.get("/api/todos").json() == []
    [done] = app_with_sqlite.get("/api/todos", params={"status": "done"}).json()
    assert done["done_at"] is not None


def test_overdue_todos_remind_the_reviewer_once_a_week_and_can_be_dismissed(
    app_with_sqlite: TestClient,
):
    from app.db.models import Notification
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    _open(NOV)
    rid, sp = _admins_round(app_with_sqlite)
    app_with_sqlite.put(
        f"/api/reviews/{rid}/items/{sp['id']}", json={"decision": "change", "note": "Use PIM"}
    )
    app_with_sqlite.post(f"/api/reviews/{rid}/finish")
    with SessionLocal() as db:
        from app.db.models import Todo

        db.query(Todo).update({Todo.due_at: NOV - timedelta(days=1)})
        db.commit()

    _open(NOV)
    _open(NOV + timedelta(hours=1))  # same week: no second reminder
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.kind == "todo_overdue").count() == 1
    [todo] = app_with_sqlite.get("/api/todos").json()
    assert todo["overdue"] and todo["action"] == "change"
    assert app_with_sqlite.get("/api/todos/summary").json()["overdue"] == 1

    url = f"/api/todos/{todo['id']}/dismiss"
    assert app_with_sqlite.post(url, json={"reason": ""}).status_code == 422
    out = app_with_sqlite.post(url, json={"reason": "Break-glass account, kept on purpose"}).json()
    assert (out["status"], out["dismissed_reason"]) == (
        "dismissed",
        "Break-glass account, kept on purpose",
    )
    assert app_with_sqlite.post(url, json={"reason": "again"}).status_code == 409
    csv = app_with_sqlite.get("/api/todos/export").text.splitlines()
    assert csv[0].startswith("status,overdue,action") and csv[1].startswith("dismissed")


def test_a_grant_gone_before_the_finish_makes_no_todo(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    _open(NOV)
    rid, sp = _admins_round(app_with_sqlite)
    app_with_sqlite.put(f"/api/reviews/{rid}/items/{sp['id']}", json={"decision": "remove"})
    with SessionLocal() as db:
        db.query(RoleAssignment).filter(func.lower(RoleAssignment.principal_id) == "sp-1").delete(
            synchronize_session=False
        )
        db.commit()
    _open(NOV)
    app_with_sqlite.post(f"/api/reviews/{rid}/finish")
    assert app_with_sqlite.get("/api/todos", params={"status": "all"}).json() == []


def test_signed_in_people_see_and_dismiss_only_their_todos(app_with_sqlite: TestClient, signed_in):
    _world(app_with_sqlite)
    _open(NOV)
    adm = signed_in("admin-id", admin=True)
    rounds = {
        r["scope_id"]: r["id"] for r in app_with_sqlite.get("/api/reviews", headers=adm).json()
    }
    for rid in rounds.values():
        for i in app_with_sqlite.get(f"/api/reviews/{rid}", headers=adm).json()["items"]:
            app_with_sqlite.put(
                f"/api/reviews/{rid}/items/{i['id']}", json={"decision": "remove"}, headers=adm
            )
        assert app_with_sqlite.post(f"/api/reviews/{rid}/finish", headers=adm).status_code == 200
    admins_todo = next(
        t
        for t in app_with_sqlite.get("/api/todos", params={"show": "all"}, headers=adm).json()
        if t["principal_id"] == "sp-1"
    )

    marc = signed_in("marc-id")
    mine = app_with_sqlite.get("/api/todos", headers=marc).json()
    assert len(mine) == 2 and all(t["can_dismiss"] for t in mine)
    every = {
        t["id"]: t
        for t in app_with_sqlite.get("/api/todos", params={"show": "all"}, headers=marc).json()
    }
    # The admins' to-do (sp-1 Owner on the MG) applies to his subscription, so he sees it, but can't dismiss it.
    assert len(every) == 3 and every[admins_todo["id"]]["can_dismiss"] is False
    assert app_with_sqlite.get("/api/todos/summary", headers=marc).json()["mine_open"] == 2
    denied = app_with_sqlite.post(
        f"/api/todos/{admins_todo['id']}/dismiss", json={"reason": "x"}, headers=marc
    )
    assert denied.status_code == 403
    bob = signed_in("bob-id")
    assert (
        app_with_sqlite.post(
            f"/api/todos/{mine[0]['id']}/dismiss", json={"reason": "x"}, headers=bob
        ).status_code
        == 403
    )
