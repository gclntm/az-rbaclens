"""Requests from another website, or for another host name, are refused (core/request_guard.py)."""

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.core import request_guard as guard
from app.core.config import settings

PUT = ("/api/settings/auth", {"enabled": False})


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("localhost", True),
        ("localhost:8000", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("192.168.1.20:80", True),
        ("[::1]:8000", True),
        ("[fe80::1]", True),
        ("mymac.local", True),
        ("rbaclens.lab.example", True),  # listed in EXTRA_SERVER_NAMES below
        ("evil.example", False),
        ("localhost.evil.example", False),
        ("evil.example:80", False),
        ("local", False),
        ("-bad-.local", False),
        ("", False),
        (None, False),
    ],
)
def test_host_names_the_app_answers_to(host, ok):
    assert guard.host_allowed(host, {"rbaclens.lab.example"}) is ok


def test_extra_names_ignore_what_is_not_a_host_name():
    assert guard.extra_names("Rbaclens.Lab.example  ok.test  *.evil  a/b ") == {
        "rbaclens.lab.example",
        "ok.test",
    }


@pytest.mark.parametrize(
    ("origin", "host", "ok"),
    [
        ("http://localhost", "localhost", True),
        ("http://192.168.1.20", "192.168.1.20", True),
        ("https://rbaclens.lab.example", "rbaclens.lab.example", True),
        ("http://localhost:8080", "localhost:8080", True),
        ("http://localhost:5173", "backend:8000", True),  # listed in CORS_ORIGINS
        ("http://localhost:8025", "localhost", False),  # another port is another site
        ("http://evil.example", "localhost", False),
        ("null", "localhost", False),
        ("", "localhost", False),
        ("file://localhost", "localhost", False),
    ],
)
def test_origin_must_be_this_app(origin, host, ok):
    assert guard.from_this_app(origin, host, ["http://localhost:5173/"]) is ok


def _refusal(method: str, **headers: str):
    return guard.refusal(method, Headers({k.replace("_", "-"): v for k, v in headers.items()}))


def test_reads_are_not_checked_for_origin():
    assert _refusal("GET", host="localhost", origin="http://evil.example") is None


def test_a_script_without_origin_passes_but_a_cross_site_browser_request_does_not():
    assert _refusal("POST", host="localhost") is None
    assert _refusal("POST", host="localhost", sec_fetch_site="same-origin") is None
    assert _refusal("POST", host="localhost", sec_fetch_site="cross-site") == (
        403,
        guard.ORIGIN_REFUSED,
    )


def test_a_change_from_another_website_is_refused_and_recorded(app_with_sqlite: TestClient, caplog):
    path, body = PUT
    with caplog.at_level(logging.WARNING, logger="app.core.request_guard"):
        r = app_with_sqlite.put(path, json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"detail": guard.ORIGIN_REFUSED}
    assert "refused PUT /api/settings/auth" in caplog.text
    # Refused, yet in the audit log with its status.
    events = app_with_sqlite.get("/api/audit/events").json()
    assert (events[0]["method"], events[0]["path"], events[0]["status_code"]) == ("PUT", path, 403)


def test_a_change_from_the_app_itself_passes(app_with_sqlite: TestClient):
    path, body = PUT
    assert (
        app_with_sqlite.put(path, json=body, headers={"Origin": "http://testserver"}).status_code
        == 200
    )
    assert app_with_sqlite.put(path, json=body).status_code == 200  # curl, a script


def test_a_listed_origin_passes(app_with_sqlite: TestClient, monkeypatch):
    path, body = PUT
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:5173"])
    assert (
        app_with_sqlite.put(
            path, json=body, headers={"Origin": "http://localhost:5173"}
        ).status_code
        == 200
    )


def test_an_unknown_host_name_is_refused_even_for_reads(app_with_sqlite: TestClient):
    r = app_with_sqlite.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.json() == {"detail": guard.HOST_REFUSED}
    assert app_with_sqlite.get("/api/health", headers={"Host": "192.168.1.20"}).status_code == 200
    assert app_with_sqlite.get("/api/health", headers={"Host": "mymac.local"}).status_code == 200


def test_sign_in_off_is_logged_at_start(app_with_sqlite: TestClient, caplog):
    from app.core import security

    with caplog.at_level(logging.WARNING):
        assert security.warn_if_open() is True
    assert "Sign-in is off" in caplog.text


def test_switching_sign_in_off_is_logged_once(app_with_sqlite: TestClient, signed_in, caplog):
    admin = signed_in("ada", admin=True)
    on = {
        "enabled": True,
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "admin_group_ids": ["44444444-4444-4444-4444-444444444444"],
    }
    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        # Saving "off" while it is off says nothing new; switching it off does.
        assert (
            app_with_sqlite.put(
                "/api/settings/auth", json={"enabled": False}, headers=admin
            ).status_code
            == 200
        )
        assert "Sign-in is off" not in caplog.text
        assert app_with_sqlite.put("/api/settings/auth", json=on, headers=admin).status_code == 200
        assert (
            app_with_sqlite.put(
                "/api/settings/auth", json={"enabled": False}, headers=admin
            ).status_code
            == 200
        )
    assert caplog.text.count("Sign-in is off") == 1


def test_no_warning_when_sign_in_is_on(app_with_sqlite: TestClient, caplog):
    from app.core import security

    app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "enabled": True,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": "22222222-2222-2222-2222-222222222222",
            "admin_group_ids": ["44444444-4444-4444-4444-444444444444"],
        },
    )
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        assert security.warn_if_open() is False
    assert "Sign-in is off" not in caplog.text
