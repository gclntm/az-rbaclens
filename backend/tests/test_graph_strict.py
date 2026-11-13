"""A check must fail when Microsoft Graph can't be reached, not go on without names or members.

Without names every identity reads as "no longer exists in Entra ID: remove".
A refusal (the app lacks the permission) is a stable fact and stays tolerated.
"""

from __future__ import annotations

import httpx
import pytest

import app.tools.graph_groups as groups_mod
import app.tools.graph_resolver as resolver_mod
from app.tools.graph_resolver import GraphUnavailable


class _Cred:
    def get_token(self, *_a, **_k):
        return type("T", (), {"token": "t"})()


class _NoToken:
    def get_token(self, *_a, **_k):
        raise RuntimeError("TLS handshake failed")


_REAL_CLIENT = httpx.Client  # both modules share ``httpx``: keep the unpatched class


def _client(handler):
    """An ``httpx.Client`` factory whose requests go to ``handler``."""
    return lambda **_k: _REAL_CLIENT(transport=httpx.MockTransport(handler))


def test_names_no_token(monkeypatch):
    assert resolver_mod.resolve_principals({"u1": "User"}, _NoToken()) == {}
    with pytest.raises(GraphUnavailable, match="no token"):
        resolver_mod.resolve_principals({"u1": "User"}, _NoToken(), strict=True)


def test_names_network_error_or_server_error_stops_a_strict_lookup(monkeypatch):
    def down(_request):
        raise httpx.ConnectError("proxy unreachable")

    monkeypatch.setattr(resolver_mod.httpx, "Client", _client(down))
    assert resolver_mod.resolve_principals({"u1": "User"}, _Cred()) == {}
    with pytest.raises(GraphUnavailable, match="did not answer"):
        resolver_mod.resolve_principals({"u1": "User"}, _Cred(), strict=True)

    monkeypatch.setattr(resolver_mod.httpx, "Client", _client(lambda _r: httpx.Response(503)))
    with pytest.raises(GraphUnavailable):
        resolver_mod.resolve_principals({"u1": "User"}, _Cred(), strict=True)


def test_names_a_refusal_is_not_an_outage(monkeypatch):
    monkeypatch.setattr(resolver_mod.httpx, "Client", _client(lambda _r: httpx.Response(403)))
    assert resolver_mod.resolve_principals({"u1": "User"}, _Cred(), strict=True) == {}


def test_members_strict(monkeypatch):
    def down(_request):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(groups_mod.httpx, "Client", _client(down))
    assert groups_mod.resolve_group_memberships(["g1"], _Cred()) == {"g1": None}
    with pytest.raises(GraphUnavailable, match="members of a group"):
        groups_mod.resolve_group_memberships(["g1"], _Cred(), strict=True)
    with pytest.raises(GraphUnavailable, match="no token"):
        groups_mod.resolve_group_memberships(["g1"], _NoToken(), strict=True)

    # Refused or deleted: "unknown", as before; an empty group is still an empty list.
    monkeypatch.setattr(groups_mod.httpx, "Client", _client(lambda _r: httpx.Response(403)))
    assert groups_mod.resolve_group_memberships(["g1"], _Cred(), strict=True) == {"g1": None}
    monkeypatch.setattr(
        groups_mod.httpx, "Client", _client(lambda _r: httpx.Response(200, json={"value": []}))
    )
    assert groups_mod.resolve_group_memberships(["g1"], _Cred(), strict=True) == {"g1": []}
