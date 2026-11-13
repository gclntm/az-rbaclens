"""Responsible people: inheritance, tag suggestions, admin-only changes, /me."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.tools.graph_resolver import EntraUser, looks_like_email
from tests.test_registry import MG, RG, SUB_SCOPE, _seed_scan

MARC = EntraUser("marc-id", "Marc Dubois", "marc@example.org")
ANA = EntraUser("ana-id", "Ana Ruiz", "ana.ruiz@example.org")
PEOPLE = {"marc@example.org": MARC, "marc-id": MARC, "ana.ruiz@example.org": ANA}


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake Graph: records every lookup."""
    from app.core import responsible

    asked: list[str] = []

    def lookup():
        def find(value: str):
            asked.append(value)
            return PEOPLE.get(value.lower())

        return find

    monkeypatch.setattr(responsible, "graph_lookup", lookup)
    return asked


def _world(client: TestClient, tags: dict[str, dict[str, str]] | None = None) -> None:
    from app.db.session import SessionLocal
    from tests.test_registry import HIERARCHY

    hierarchy = {k: dict(v) for k, v in HIERARCHY.items()}
    for scope, t in (tags or {}).items():
        hierarchy[scope]["tags"] = t
    with SessionLocal() as db:
        _seed_scan(db, hierarchy=hierarchy)
        db.commit()
    client.post("/api/settings/registry/rebuild")


def _put(client: TestClient, scope: str, person: str):
    return client.put("/api/responsible", params={"scope": scope}, json={"person": person})


def test_email_check():
    assert looks_like_email("ana.ruiz@example.org")
    assert not looks_like_email("GC")
    assert not looks_like_email("a'b@example.org")  # never reaches a Graph $filter


def test_set_inherits_down_and_lower_setting_wins(app_with_sqlite: TestClient, graph):
    _world(app_with_sqlite)
    assert _put(app_with_sqlite, SUB_SCOPE, "marc@example.org").status_code == 200
    rg = app_with_sqlite.get("/api/responsible/of", params={"scope": RG}).json()
    assert (rg["responsible"]["person_name"], rg["from_scope_id"], rg["from_scope_name"]) == (
        "Marc Dubois",
        SUB_SCOPE,
        "prod",
    )
    assert (
        app_with_sqlite.get("/api/responsible/of", params={"scope": MG}).json()["responsible"]
        is None
    )

    _put(app_with_sqlite, RG, "ana.ruiz@example.org")
    tree = {n["scope_id"]: n for n in app_with_sqlite.get("/api/responsible/tree").json()}
    assert list(tree) == [MG, SUB_SCOPE, RG]  # no single resources
    assert (
        tree[RG]["responsible"]["person_name"],
        tree[RG]["from_scope_id"],
        tree[RG]["depth"],
    ) == ("Ana Ruiz", RG, 2)
    assert tree[SUB_SCOPE]["responsible"]["source"] == "manual"

    assert app_with_sqlite.delete("/api/responsible", params={"scope": RG}).status_code == 204
    rg = app_with_sqlite.get("/api/responsible/of", params={"scope": RG}).json()
    assert rg["responsible"]["person_name"] == "Marc Dubois"
    assert app_with_sqlite.delete("/api/responsible", params={"scope": RG}).status_code == 404


def test_unknown_person_or_place_is_refused(app_with_sqlite: TestClient, graph):
    _world(app_with_sqlite)
    assert _put(app_with_sqlite, SUB_SCOPE, "nobody@example.org").status_code == 400
    assert _put(app_with_sqlite, "/subscriptions/nope", "marc@example.org").status_code == 404


def test_tag_suggestions_match_emails_and_remember_decisions(app_with_sqlite: TestClient, graph):
    from app.core.responsible import graph_lookup, refresh_suggestions  # graph_lookup is the fake
    from app.db.session import SessionLocal

    _world(app_with_sqlite, {SUB_SCOPE: {"Owner": "ana.ruiz@example.org"}, RG: {"owner": "GC"}})
    with SessionLocal() as db:
        assert refresh_suggestions(db, graph_lookup()) == 2
    s = app_with_sqlite.get("/api/responsible/suggestions").json()
    assert s["tag_name"] == "owner"
    assert [(x["scope_id"], x["person_name"]) for x in s["open"]] == [(SUB_SCOPE, "Ana Ruiz")]
    assert [(x["scope_id"], x["tag_value"]) for x in s["unmatched"]] == [(RG, "GC")]
    assert graph == ["ana.ruiz@example.org"]  # "GC" is not an email: never looked up

    used = app_with_sqlite.post(
        "/api/responsible/suggestions/use", json={"scope": SUB_SCOPE}
    ).json()
    assert (used["responsible"]["person_name"], used["responsible"]["source"]) == (
        "Ana Ruiz",
        "tag",
    )
    assert (
        app_with_sqlite.post("/api/responsible/suggestions/ignore", json={"scope": RG}).status_code
        == 204
    )
    with SessionLocal() as db:
        refresh_suggestions(db, graph_lookup())  # same values: decisions stick, no new lookups
    s = app_with_sqlite.get("/api/responsible/suggestions").json()
    assert (s["open"], s["unmatched"], [x["scope_id"] for x in s["ignored"]]) == ([], [], [RG])
    assert graph == ["ana.ruiz@example.org"]


def test_tag_name_is_a_setting_and_rereads_tags(app_with_sqlite: TestClient, graph):
    _world(
        app_with_sqlite, {SUB_SCOPE: {"owner": "x@example.org", "responsible": "marc@example.org"}}
    )
    assert (
        app_with_sqlite.put("/api/responsible/settings", json={"tag_name": "bad/name"}).status_code
        == 400
    )
    r = app_with_sqlite.put("/api/responsible/settings", json={"tag_name": "Responsible"})
    assert r.status_code == 200 and r.json()["tag_name"] == "Responsible"
    s = app_with_sqlite.get(
        "/api/responsible/suggestions"
    ).json()  # refreshed in the background task
    assert s["tag_name"] == "Responsible"
    assert [(x["scope_id"], x["person_name"]) for x in s["open"]] == [(SUB_SCOPE, "Marc Dubois")]


def test_changes_need_an_admin_when_sign_in_restricts_admins(
    app_with_sqlite: TestClient, graph, monkeypatch
):
    from app.core import security

    _world(app_with_sqlite)

    class Row:
        enabled = True
        tenant_id = "t"
        client_id = "c"
        allowed_group_ids = None
        admin_group_ids = "admins"

    monkeypatch.setattr(security, "load_auth_settings", lambda _s: Row())
    monkeypatch.setattr(
        security, "verify_token", lambda *_a, **_k: {"oid": "marc-id", "name": "Marc", "groups": []}
    )
    headers = {"Authorization": "Bearer x"}
    r = app_with_sqlite.put(
        "/api/responsible", params={"scope": SUB_SCOPE}, json={"person": "marc-id"}, headers=headers
    )
    assert r.status_code == 403
    assert app_with_sqlite.get("/api/responsible/tree", headers=headers).status_code == 200


def test_me_without_and_with_sign_in(app_with_sqlite: TestClient, graph, monkeypatch):

    from app.core import security

    _world(app_with_sqlite)
    assert app_with_sqlite.get("/api/me").json() == {
        "signed_in": False,
        "oid": None,
        "name": None,
        "is_admin": True,
        "responsible_for": [],
    }
    _put(app_with_sqlite, SUB_SCOPE, "marc@example.org")

    class Row:
        enabled = True
        tenant_id = "t"
        client_id = "c"
        allowed_group_ids = None
        admin_group_ids = "admins"

    monkeypatch.setattr(security, "load_auth_settings", lambda _s: Row())
    monkeypatch.setattr(
        security,
        "verify_token",
        lambda *_a, **_k: {"oid": "MARC-ID", "name": "Marc Dubois", "groups": []},
    )
    me = app_with_sqlite.get("/api/me", headers={"Authorization": "Bearer x"}).json()
    assert (me["signed_in"], me["oid"], me["is_admin"]) == (True, "marc-id", False)
    assert me["responsible_for"] == [
        {"scope_id": SUB_SCOPE, "name": "prod", "level": "subscription"}
    ]
