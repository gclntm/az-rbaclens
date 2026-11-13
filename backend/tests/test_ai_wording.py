"""Phase 14: optional AI wording. Claude is never called here: the client is a fake."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core import ai_wording
from tests.test_reviews import _world


class FakeMessages:
    def __init__(self, calls: list[dict[str, Any]], stop: str = "end_turn") -> None:
        self.calls, self.stop = calls, stop

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        content = (
            []
            if self.stop == "refusal"
            else [SimpleNamespace(type="text", text=" In plain words: remove it. ")]
        )
        return SimpleNamespace(content=content, stop_reason=self.stop, model=kw["model"])


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    state = {"stop": "end_turn", "keys": []}

    def client(api_key: str) -> Any:
        state["keys"].append(api_key)
        msgs = FakeMessages(calls, state["stop"])
        return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs))

    monkeypatch.setattr(ai_wording, "_client", client)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "encryption_key", Fernet.generate_key().decode())


def _sp_grant() -> str:
    from app.core.current_view import CurrentView, grant_id
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return next(grant_id(r) for r in CurrentView(db).ras if r.principal_id.lower() == "sp-1")


def test_off_by_default_and_nothing_is_sent(app_with_sqlite: TestClient, fake_claude):
    _world(app_with_sqlite)
    assert app_with_sqlite.get("/api/ai/status").json() == {"available": False, "model": None}
    s = app_with_sqlite.get("/api/settings/ai").json()
    assert (s["enabled"], s["model"], s["key_source"]) == (False, "claude-opus-5-5", None)
    r = app_with_sqlite.post("/api/ai/explain", json={"grant_id": _sp_grant()})
    assert r.status_code == 409 and "off" in r.json()["detail"]
    assert fake_claude.calls == []


def test_the_key_is_stored_encrypted_and_never_returned(
    app_with_sqlite: TestClient, fake_claude, encryption
):
    from app.db.models import AppSetting
    from app.db.session import SessionLocal

    r = app_with_sqlite.put(
        "/api/settings/ai",
        json={"enabled": True, "model": "claude-opus-5-5", "api_key": "sk-ant-test-123"},
    )
    assert r.status_code == 200 and r.json()["key_source"] == "stored"
    assert "sk-ant" not in r.text
    with SessionLocal() as db:
        stored = json.dumps(db.get(AppSetting, "ai").value)
    assert "sk-ant-test-123" not in stored
    # Saving again without a key keeps it; "" clears it.
    assert (
        app_with_sqlite.put(
            "/api/settings/ai", json={"enabled": True, "model": "claude-haiku-5-5"}
        ).json()["key_source"]
        == "stored"
    )
    assert (
        app_with_sqlite.put(
            "/api/settings/ai", json={"enabled": True, "model": "claude-haiku-5-5", "api_key": ""}
        ).json()["key_source"]
        is None
    )
    assert (
        app_with_sqlite.put(
            "/api/settings/ai", json={"enabled": True, "model": "gpt-4"}
        ).status_code
        == 400
    )


def test_a_key_needs_encryption(app_with_sqlite: TestClient, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "encryption_key", "")
    r = app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-opus-5-5", "api_key": "k"}
    )
    assert r.status_code == 400 and "ENCRYPTION_KEY" in r.json()["detail"]


def test_explain_sends_no_name_or_id_uses_fallbacks_and_caches(
    app_with_sqlite: TestClient, fake_claude, encryption
):
    _world(app_with_sqlite)
    app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-opus-5-5", "api_key": "k-1"}
    )
    gid = _sp_grant()

    preview = app_with_sqlite.post("/api/ai/preview", json={"grant_id": gid}).json()
    assert preview["facts"]["who"] == "an app" and preview["facts"]["role"] == "Owner"
    assert fake_claude.calls == []  # a preview sends nothing

    out = app_with_sqlite.post("/api/ai/explain", json={"grant_id": gid}).json()
    assert out == {
        "text": "In plain words: remove it.",
        "model": "claude-opus-5-5",
        "cached": False,
    }
    [call] = fake_claude.calls
    assert fake_claude.state["keys"] == ["k-1"]
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "low"} and "thinking" not in call
    sent = call["system"] + json.dumps(call["messages"])
    for secret in ("sp-1", "SP-1", "sp-one"):  # the app's object id and display name
        assert secret not in sent
    assert json.loads(call["messages"][0]["content"]) == preview["facts"]

    again = app_with_sqlite.post("/api/ai/explain", json={"grant_id": gid}).json()
    assert again["cached"] is True and len(fake_claude.calls) == 1


def test_haiku_has_no_server_side_fallback_and_identity_wording(
    app_with_sqlite: TestClient, fake_claude, encryption
):
    _world(app_with_sqlite)
    app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-haiku-5-5", "api_key": "k"}
    )
    out = app_with_sqlite.post("/api/ai/explain", json={"principal_id": "alice-id"}).json()
    assert out["model"] == "claude-haiku-5-5"
    [call] = fake_claude.calls
    assert "fallbacks" not in call and "betas" not in call
    facts = json.loads(call["messages"][0]["content"])
    assert facts["who"] == "a person" and "alice" not in call["messages"][0]["content"]


def test_a_refusal_or_missing_target_is_reported(
    app_with_sqlite: TestClient, fake_claude, encryption
):
    _world(app_with_sqlite)
    app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-opus-5-5", "api_key": "k"}
    )
    fake_claude.state["stop"] = "refusal"
    r = app_with_sqlite.post("/api/ai/explain", json={"grant_id": _sp_grant()})
    assert r.status_code == 502 and "declined" in r.json()["detail"]
    assert app_with_sqlite.post("/api/ai/explain", json={"grant_id": "nope"}).status_code == 404
    assert app_with_sqlite.post("/api/ai/explain", json={}).status_code == 422


def test_the_environment_key_is_used_when_none_is_stored(
    app_with_sqlite: TestClient, fake_claude, monkeypatch
):
    _world(app_with_sqlite)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    app_with_sqlite.put("/api/settings/ai", json={"enabled": True, "model": "claude-sonnet-5-5"})
    assert app_with_sqlite.get("/api/settings/ai").json()["key_source"] == "environment"
    assert app_with_sqlite.get("/api/ai/status").json() == {
        "available": True,
        "model": "claude-sonnet-5-5",
    }
    app_with_sqlite.post("/api/ai/explain", json={"principal_id": "sp-1"})
    assert fake_claude.state["keys"] == ["env-key"]


def test_signed_in_people_only_get_wording_for_what_they_see(
    app_with_sqlite: TestClient, fake_claude, signed_in, monkeypatch
):
    _world(app_with_sqlite)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    adm = signed_in("admin-id", admin=True)
    app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-opus-5-5"}, headers=adm
    )
    bob = signed_in("bob-id")  # responsible for nothing
    assert app_with_sqlite.get("/api/ai/status", headers=bob).status_code == 200
    assert (
        app_with_sqlite.post(
            "/api/ai/explain", json={"grant_id": _sp_grant()}, headers=bob
        ).status_code
        == 403
    )
    assert (
        app_with_sqlite.put(
            "/api/settings/ai", json={"enabled": False, "model": "claude-opus-5-5"}, headers=bob
        ).status_code
        == 403
    )
    assert fake_claude.calls == []


def test_new_texts_stop_at_the_daily_limit_but_written_ones_still_show(
    app_with_sqlite: TestClient, fake_claude, encryption, monkeypatch
):
    from app.core.config import settings

    _world(app_with_sqlite)
    monkeypatch.setattr(settings, "ai_daily_limit", 1)
    app_with_sqlite.put(
        "/api/settings/ai", json={"enabled": True, "model": "claude-opus-5-5", "api_key": "k-1"}
    )
    gid = _sp_grant()
    assert app_with_sqlite.post("/api/ai/explain", json={"grant_id": gid}).status_code == 200
    s = app_with_sqlite.get("/api/settings/ai").json()
    assert (s["daily_limit"], s["written_today"]) == (1, 1)

    from app.db.models import Principal
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        pid = next(p.id for p in db.query(Principal).all() if p.id.lower() != "sp-1")
    refused = app_with_sqlite.post("/api/ai/explain", json={"principal_id": pid})
    assert refused.status_code == 409 and "limit of 1" in refused.json()["detail"]
    assert len(fake_claude.calls) == 1  # nothing sent once the limit is reached
    again = app_with_sqlite.post("/api/ai/explain", json={"grant_id": gid}).json()
    assert again["cached"] is True  # already written: still shown
