"""Tests for the scope-browser endpoints.

We don't talk to real Azure here — the ``mg_client.entities.list()`` SDK
call is monkey-patched to return synthetic data. That lets us exercise the entity-split, MG
depth computation, descendant filtering, and the TTL cache without a
tenant.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_entities(monkeypatch):
    """Inject a fake Azure entity tree:

    root  (mg)
    ├── mg-contoso (mg)
    │   ├── mg-platform (mg)
    │   │   └── sub-platform (sub)
    │   └── mg-infra (mg)
    └── mg-orphan (mg, no subs)
    └── sub-floating (sub, parent: root — directly under root)
    """
    from app.api import scope_browser

    def make_mg(name, display, parent):
        return SimpleNamespace(
            name=name,
            display_name=display,
            type="Microsoft.Management/managementGroups",
            parent=SimpleNamespace(name=parent, id=None) if parent else None,
        )

    def make_sub(name, display, parent):
        return SimpleNamespace(
            name=name,
            display_name=display,
            type="/subscriptions",
            parent=SimpleNamespace(name=parent, id=None) if parent else None,
        )

    entities = [
        make_mg("root", "Tenant Root", None),
        make_mg("mg-contoso", "Contoso", "root"),
        make_mg("mg-platform", "Platform", "mg-contoso"),
        make_mg("mg-infra", "Infra", "mg-contoso"),
        make_mg("mg-orphan", "Orphan", "root"),
        make_sub("sub-platform", "Platform sub", "mg-platform"),
        make_sub("sub-floating", "Floating sub", "root"),
    ]

    class FakeMgClient:
        def __init__(self, *_a, **_kw):
            self.entities = SimpleNamespace(list=lambda: iter(entities))

    monkeypatch.setattr(scope_browser, "ManagementGroupsAPI", FakeMgClient)
    monkeypatch.setattr(scope_browser, "get_credential", lambda: object())
    scope_browser._invalidate_cache()
    yield entities
    scope_browser._invalidate_cache()


def test_mgmt_groups_tree_with_depths(fake_entities):
    """The management-group tree behind the ``?mgmt_group=`` filter."""
    from app.api import scope_browser

    mgs, _ = scope_browser._fetch_entities()
    rows = [m.model_dump() for m in mgs]
    by_id = {r["id"]: r for r in rows}

    # All five MGs present.
    assert set(by_id) == {"root", "mg-contoso", "mg-platform", "mg-infra", "mg-orphan"}

    # Depths follow the parent chain.
    assert by_id["root"]["depth"] == 0
    assert by_id["root"]["parent_id"] is None
    assert by_id["mg-contoso"]["depth"] == 1
    assert by_id["mg-contoso"]["parent_id"] == "root"
    assert by_id["mg-platform"]["depth"] == 2
    assert by_id["mg-platform"]["parent_id"] == "mg-contoso"
    assert by_id["mg-orphan"]["depth"] == 1

    # Scope is normalized lower-case full ARM path.
    assert (
        by_id["mg-platform"]["scope"]
        == "/providers/microsoft.management/managementgroups/mg-platform"
    )

    # Display names + tree-walk ordering (siblings are alphabetical
    # within their parent, parents come before children).
    ordered = [r["id"] for r in rows]
    assert ordered.index("mg-contoso") < ordered.index("mg-platform")
    assert ordered.index("mg-contoso") < ordered.index("mg-infra")
    # mg-infra alphabetically < mg-platform under the same parent.
    assert ordered.index("mg-infra") < ordered.index("mg-platform")


def test_list_subscriptions_all(app_with_sqlite: TestClient, fake_entities):
    rows = app_with_sqlite.get("/api/scope-browser/subscriptions").json()
    ids = {r["id"] for r in rows}
    assert ids == {"sub-platform", "sub-floating"}
    p = next(r for r in rows if r["id"] == "sub-platform")
    assert p["mgmt_group_id"] == "mg-platform"
    assert p["scope"] == "/subscriptions/sub-platform"


def test_list_subscriptions_filtered_by_ancestor_mg(app_with_sqlite: TestClient, fake_entities):
    """Picking ``mg-contoso`` should include ``sub-platform`` (under
    mg-platform, which is under mg-contoso) — i.e. transitive descendants."""
    rows = app_with_sqlite.get(
        "/api/scope-browser/subscriptions", params={"mgmt_group": "mg-contoso"}
    ).json()
    ids = {r["id"] for r in rows}
    assert ids == {"sub-platform"}
    assert "sub-floating" not in ids


def test_list_subscriptions_filtered_by_direct_mg(app_with_sqlite: TestClient, fake_entities):
    rows = app_with_sqlite.get(
        "/api/scope-browser/subscriptions", params={"mgmt_group": "mg-platform"}
    ).json()
    assert {r["id"] for r in rows} == {"sub-platform"}


def test_list_subscriptions_filter_with_no_subs_returns_empty(
    app_with_sqlite: TestClient, fake_entities
):
    rows = app_with_sqlite.get(
        "/api/scope-browser/subscriptions", params={"mgmt_group": "mg-orphan"}
    ).json()
    assert rows == []


def test_cache_returns_same_result_within_ttl(
    app_with_sqlite: TestClient, fake_entities, monkeypatch
):
    """Second call within TTL must not hit the (mocked) SDK again."""
    from app.api import scope_browser

    call_count = {"n": 0}
    real_fetch = scope_browser._fetch_entities

    def counting_fetch():
        call_count["n"] += 1
        return real_fetch()

    monkeypatch.setattr(scope_browser, "_fetch_entities", counting_fetch)
    scope_browser._invalidate_cache()

    app_with_sqlite.get("/api/scope-browser/subscriptions")
    app_with_sqlite.get("/api/scope-browser/subscriptions")
    app_with_sqlite.get("/api/scope-browser/subscriptions", params={"mgmt_group": "root"})
    # 3 hits, all sharing the "entities" cache key, so only one underlying fetch.
    assert call_count["n"] == 1


def test_refresh_cache_endpoint_clears_cache(
    app_with_sqlite: TestClient, fake_entities, monkeypatch
):
    from app.api import scope_browser

    call_count = {"n": 0}
    real_fetch = scope_browser._fetch_entities

    def counting_fetch():
        call_count["n"] += 1
        return real_fetch()

    monkeypatch.setattr(scope_browser, "_fetch_entities", counting_fetch)
    scope_browser._invalidate_cache()

    app_with_sqlite.get("/api/scope-browser/subscriptions")
    app_with_sqlite.post("/api/scope-browser/refresh")
    app_with_sqlite.get("/api/scope-browser/subscriptions")
    assert call_count["n"] == 2
