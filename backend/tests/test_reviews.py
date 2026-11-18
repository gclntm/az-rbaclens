"""Monthly reviews: routing, no self-review, decisions, carry-over, gone grants, finish."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func

from app.core.findings import grant_fingerprint
from app.tools.graph_resolver import EntraUser
from tests.test_registry import MG, OWNER, READER, RG, SUB_SCOPE, _arm, _seed_world

MARC = EntraUser("marc-id", "Marc Dubois", "marc@example.org")
ALICE = EntraUser("alice-id", "Alice", "alice@example.com")
OCT = datetime(2026, 10, 15, 12, tzinfo=UTC)
NOV = datetime(2026, 11, 2, 12, tzinfo=UTC)


def _world(client: TestClient) -> None:
    """_seed_world (Owner@MG sp-1, Reader@sub alice, Reader@rg grp-1 = {alice, bob});
    Marc responsible for the subscription, Alice for the resource group."""
    from app.core.registry import rebuild_registries
    from app.core.responsible import set_responsible
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
        rebuild_registries(db)
        db.commit()
    with SessionLocal() as db:
        set_responsible(db, SUB_SCOPE, MARC, "manual", None)
        set_responsible(db, RG, ALICE, "manual", None)
        db.commit()


def _open(now: datetime) -> dict[str, int]:
    from app.core.reviews import open_and_sync
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return open_and_sync(db, now)


def _rounds(client: TestClient) -> dict[str | None, dict]:
    return {r["scope_id"]: r for r in client.get("/api/reviews").json()}


def _detail(client: TestClient, round_id: int) -> dict:
    return client.get(f"/api/reviews/{round_id}").json()


def test_grants_go_to_the_place_where_they_were_given_and_never_to_their_holder(
    app_with_sqlite: TestClient,
):
    _world(app_with_sqlite)
    assert _open(NOV) == {
        "rounds_opened": 2,
        "items_added": 3,
        "items_gone": 0,
        "notifications": 0,
        "todos_done": 0,
    }
    assert _open(NOV)["items_added"] == 0  # idempotent

    rounds = _rounds(app_with_sqlite)
    assert set(rounds) == {
        SUB_SCOPE,
        None,
    }  # Alice's own round would hold only her own group's grant
    assert (rounds[None]["reviewer_name"], rounds[SUB_SCOPE]["reviewer_name"]) == (
        "Admins",
        "Marc Dubois",
    )
    assert rounds[SUB_SCOPE]["period"] == "2026-11" and rounds[SUB_SCOPE]["due_at"].startswith(
        "2026-11-30"
    )

    admins = _detail(app_with_sqlite, rounds[None]["id"])["items"]
    assert [(i["principal_id"], i["role_name"], i["scope"]) for i in admins] == [
        ("sp-1", "Owner", MG)
    ]
    marc = {
        i["principal_id"]: i for i in _detail(app_with_sqlite, rounds[SUB_SCOPE]["id"])["items"]
    }
    assert set(marc) == {"alice-id", "grp-1"}
    assert (marc["grp-1"]["moved_from_scope"], marc["grp-1"]["moved_from_name"]) == (RG, "rg-app")
    assert marc["alice-id"]["moved_from_scope"] is None
    assert marc["alice-id"]["grant_id"] == grant_fingerprint(SUB_SCOPE, _arm(READER), "alice-id")


def test_decide_finish_and_export(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    rid = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    items = {i["principal_id"]: i for i in _detail(app_with_sqlite, rid)["items"]}

    def put(item, decision, note=None):
        return app_with_sqlite.put(
            f"/api/reviews/{rid}/items/{item['id']}", json={"decision": decision, "note": note}
        )

    assert put(items["alice-id"], "nope").status_code == 422
    r = put(items["grp-1"], "ask", "Does bob still need this?").json()
    assert r["decision"] == "ask" and r["ask_remind_at"] is not None
    assert (
        put(items["alice-id"], "remove")
        .json()["remove_command"]
        .startswith("az role assignment delete")
    )

    blocked = app_with_sqlite.post(f"/api/reviews/{rid}/finish")
    assert blocked.status_code == 409 and "1 still" in blocked.json()["detail"]
    put(items["grp-1"], "keep")
    done = app_with_sqlite.post(f"/api/reviews/{rid}/finish").json()
    assert (
        done["finished_at"] is not None
        and done["progress"]["keep"] == 1
        and done["progress"]["remove"] == 1
    )
    assert put(items["grp-1"], None).status_code == 409  # finished

    csv = app_with_sqlite.get(f"/api/reviews/{rid}/export").text.splitlines()
    assert csv[0].startswith("place,who,type,role,level,decision")
    assert any("remove" in line and "az role assignment delete" in line for line in csv[1:])


def test_keeping_a_grant_marks_its_findings_reviewed(app_with_sqlite: TestClient):
    from app.db.models import ReviewDecision
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    scan_id = app_with_sqlite.get("/api/iam-access/scans").json()[0]["id"]
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    _open(NOV)
    rid = _rounds(app_with_sqlite)[None]["id"]
    [item] = _detail(app_with_sqlite, rid)[
        "items"
    ]  # sp-1 Owner on the MG: a "powerful role high up" finding
    put = f"/api/reviews/{rid}/items/{item['id']}"
    app_with_sqlite.put(put, json={"decision": "keep"})
    with SessionLocal() as db:
        assert {d.decision for d in db.query(ReviewDecision).all()} == {"reviewed"}
        fingerprint = db.query(ReviewDecision).one().fingerprint

    # Undoing the keep reopens what it marked.
    app_with_sqlite.put(put, json={"decision": None})
    with SessionLocal() as db:
        assert db.query(ReviewDecision).count() == 0

    # A decision made by hand on the finding since stays.
    app_with_sqlite.put(put, json={"decision": "keep"})
    r = app_with_sqlite.put(
        f"/api/iam-access/findings/{fingerprint}/decision",
        json={"decision": "accepted_risk", "note": "Owned by the platform team"},
    )
    assert r.status_code == 200
    app_with_sqlite.put(put, json={"decision": "remove"})
    with SessionLocal() as db:
        assert {d.decision for d in db.query(ReviewDecision).all()} == {"accepted_risk"}


def test_kept_last_month_and_unchanged_starts_as_kept(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(OCT)
    oct_round = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    alice = next(
        i for i in _detail(app_with_sqlite, oct_round)["items"] if i["principal_id"] == "alice-id"
    )
    app_with_sqlite.put(f"/api/reviews/{oct_round}/items/{alice['id']}", json={"decision": "keep"})

    _open(NOV)
    nov = next(
        r
        for r in app_with_sqlite.get("/api/reviews", params={"period": "2026-11"}).json()
        if r["scope_id"] == SUB_SCOPE
    )
    items = {i["principal_id"]: i for i in _detail(app_with_sqlite, nov["id"])["items"]}
    assert (items["alice-id"]["decision"], items["alice-id"]["carried"]) == ("keep", True)
    assert items["grp-1"]["decision"] is None


def test_a_grant_removed_in_azure_is_marked_gone_and_does_not_block(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    _open(NOV)
    with SessionLocal() as db:
        db.query(RoleAssignment).filter(func.lower(RoleAssignment.principal_id) == "sp-1").delete(
            synchronize_session=False
        )
        db.commit()
    assert _open(NOV)["items_gone"] == 1
    admins = _rounds(app_with_sqlite)[None]
    assert (admins["progress"]["gone"], admins["blocking"]) == (1, 0)
    assert app_with_sqlite.post(f"/api/reviews/{admins['id']}/finish").status_code == 200


def test_only_the_reviewer_decides(app_with_sqlite: TestClient, signed_in):
    _world(app_with_sqlite)
    _open(NOV)
    marc = signed_in("marc-id")
    rounds = {r["scope_id"]: r for r in app_with_sqlite.get("/api/reviews", headers=marc).json()}
    assert (
        list(rounds) == [SUB_SCOPE] and rounds[SUB_SCOPE]["can_decide"]
    )  # the admins' round is not his to see
    mine = app_with_sqlite.get("/api/reviews", params={"mine": "true"}, headers=marc).json()
    assert [r["scope_id"] for r in mine] == [SUB_SCOPE]
    item = _detail_h(app_with_sqlite, rounds[SUB_SCOPE]["id"], marc)["items"][0]
    url = f"/api/reviews/{rounds[SUB_SCOPE]['id']}/items/{item['id']}"
    assert app_with_sqlite.put(url, json={"decision": "keep"}, headers=marc).status_code == 200
    bob = signed_in("bob-id")
    assert app_with_sqlite.put(url, json={"decision": "remove"}, headers=bob).status_code == 403


def _detail_h(client: TestClient, round_id: int, headers: dict) -> dict:
    return client.get(f"/api/reviews/{round_id}", headers=headers).json()


def test_grant_page_shows_this_months_review_item(app_with_sqlite: TestClient, monkeypatch):
    from app.core import reviews

    _world(app_with_sqlite)
    now = datetime.now(UTC)
    _open(now)
    gid = grant_fingerprint(MG, _arm(OWNER), "sp-1")
    ref = app_with_sqlite.get(f"/api/grants/{gid}").json()["review"]
    assert ref["reviewer_name"] == "Admins" and ref["decision"] is None and ref["can_decide"]
    assert ref["period"] == reviews.period_of(now)
