"""Email channel: message text, addresses, Graph call, delivery rules, test message."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app.core import notify
from app.core.notify import Undeliverable, deliver_pending
from app.core.notify_email import SEND_URL, EmailNotifier, compose
from app.db.models import Notification


class Cred:
    def get_token(self, *_a):
        class T:
            token = "tok"

        return T()


class Resp:
    def __init__(self, code=202, text=""):
        self.status_code, self.text = code, text


@pytest.fixture(autouse=True)
def _unpause():
    notify._paused_until = None
    yield
    notify._paused_until = None


def _n(kind="ask", recipient="alice-id", **payload) -> Notification:
    base = {
        "role": "Owner",
        "place": "prod",
        "who": "Alice",
        "period": "2026-11",
        "reviewer": "Marc",
        "path": "/reviews/3",
        "to_the_person": True,
    }
    return Notification(
        kind=kind,
        recipient_id=recipient,
        subject_ref="review:3",
        payload={**base, **payload},
        dedupe_key=f"{kind}:{recipient}",
        created_at=datetime.now(UTC),
    )


def test_compose_words_each_kind_and_escapes():
    subject, body = compose(_n(note="<b>still?</b>"), "http://lens.example")
    assert subject == "Do you still need Owner on prod?"
    assert "&lt;b&gt;still?&lt;/b&gt;" in body and "<b>still?" not in body
    assert 'href="http://lens.example/reviews/3"' in body
    assert (
        compose(_n("review_opened", place=None), "")[0]
        == "Your review of the unassigned places for 2026-11 is open"
    )
    assert "Open it in RBAC Lens" not in compose(_n("review_due", left=4), "")[1]
    assert compose(_n("reminder"), "")[0] == "Still open: the question about Alice"
    assert compose(_n(to_the_person=False), "")[0] == "Question about Alice on prod"


def test_send_looks_up_the_address_and_posts_as_the_sender():
    posts = []
    sender = EmailNotifier(
        Cred(),
        "rbaclens@example.org",
        admin_email="admins@example.org",
        address_of=lambda oid: {"alice-id": "alice@example.org"}.get(oid),
        post=lambda url, body, token: posts.append((url, body, token)) or Resp(),
    )
    sender.send(_n())
    sender.send(_n("review_opened", recipient=None))
    assert [p[0] for p in posts] == [SEND_URL.format(sender="rbaclens@example.org")] * 2
    assert [p[1]["message"]["toRecipients"][0]["emailAddress"]["address"] for p in posts] == [
        "alice@example.org",
        "admins@example.org",
    ]
    assert posts[0][2] == "tok" and posts[0][1]["saveToSentItems"] is False
    with pytest.raises(Undeliverable):
        sender.send(_n(recipient="ghost-id"))
    with pytest.raises(RuntimeError):
        EmailNotifier(
            Cred(),
            "s@example.org",
            address_of=lambda _o: "a@example.org",
            post=lambda *_a: Resp(403, "Access is denied"),
        ).send(_n())
    with pytest.raises(Undeliverable, match="NOTIFICATION_ADMIN_EMAIL"):
        EmailNotifier(Cred(), "s@example.org", post=lambda *_a: Resp()).send(
            _n("review_due", recipient=None)
        )


def _outbox(db, *rows):
    db.add_all(rows)
    db.commit()


def test_delivery_skips_old_closes_undeliverable_and_pauses_on_failure(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    sent: list[str] = []

    class Sender:
        channel = "email"
        fail = False

        def send(self, n):
            if n.recipient_id == "ghost":
                raise Undeliverable("no email address for this person")
            if self.fail:
                raise RuntimeError("Graph down")
            sent.append(n.dedupe_key)

    now = datetime.now(UTC)
    with SessionLocal() as db:
        old = _n(recipient="alice-id")
        old.dedupe_key, old.created_at = "old", now - timedelta(days=30)
        ghost = _n(recipient="ghost")
        ghost.dedupe_key = "ghost"
        ok = _n(recipient="alice-id")
        ok.dedupe_key = "ok"
        _outbox(db, old, ghost, ok)
        s = Sender()
        assert deliver_pending(db, s, now=now) == 1
        rows = {n.dedupe_key: n for n in db.query(Notification).all()}
        assert rows["old"].sent_at and rows["old"].error.startswith("not sent: older than")
        assert (
            rows["ghost"].sent_at
            and rows["ghost"].error == "not sent: no email address for this person"
        )
        assert rows["ok"].sent_at and rows["ok"].error is None and sent == ["ok"]

        late = _n(recipient="alice-id")
        late.dedupe_key = "late"
        _outbox(db, late)
        s.fail = True
        assert deliver_pending(db, s, now=now) == 0
        assert (
            db.query(Notification).filter_by(dedupe_key="late").one().error
            == "RuntimeError: Graph down"
        )
        s.fail = False
        assert deliver_pending(db, s, now=now + timedelta(minutes=5)) == 0  # paused
        assert deliver_pending(db, s, now=now + timedelta(minutes=16)) == 1


def test_test_message_endpoint(app_with_sqlite: TestClient, monkeypatch):
    from app.api import notifications as api

    assert (
        app_with_sqlite.post("/api/notifications/test", json={"to": "me@example.org"}).status_code
        == 409
    )
    posts = []
    sender = EmailNotifier(
        Cred(), "rbaclens@example.org", post=lambda url, body, token: posts.append(body) or Resp()
    )
    monkeypatch.setattr(api, "notifier", lambda: sender)
    assert app_with_sqlite.post("/api/notifications/test", json={"to": "nope"}).status_code == 400
    r = app_with_sqlite.post("/api/notifications/test", json={"to": "me@example.org"})
    assert r.status_code == 200 and r.json() == {"sent_to": "me@example.org"}
    assert posts[0]["message"]["subject"] == "RBAC Lens test message"
    status = app_with_sqlite.get("/api/notifications/status").json()
    assert (status["connected"], status["channel"], status["sender"]) == (
        True,
        "email",
        "rbaclens@example.org",
    )


def test_channel_needs_a_sender(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "notification_channel", "email")
    monkeypatch.setattr(settings, "notification_sender", "")
    assert notify.notifier() is None
    monkeypatch.setattr(settings, "notification_channel", "pigeon")
    assert notify.notifier() is None


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def send_message(self, msg):
        FakeSMTP.sent.append((self.host, self.port, msg))


def test_smtp_sends_html_mail_to_mailpit():
    from app.core.notify_email import SmtpNotifier

    FakeSMTP.sent = []
    sender = SmtpNotifier(
        Cred(),
        "mailpit",
        1025,
        "rbaclens@localhost",
        admin_email="admins@rbaclens.test",
        app_url="http://localhost",
        address_of=lambda oid: {"alice-id": "alice@example.org"}.get(oid),
        smtp_factory=FakeSMTP,
    )
    sender.send(_n(note="ok?"))
    sender.send(_n("review_opened", recipient=None))
    (host, port, first), (_, _, second) = FakeSMTP.sent
    assert (host, port, first["To"], first["From"]) == (
        "mailpit",
        1025,
        "alice@example.org",
        "rbaclens@localhost",
    )
    assert first["Subject"] == "Do you still need Owner on prod?"
    html_part = first.get_body(preferencelist=("html",)).get_content()
    assert "http://localhost/reviews/3" in html_part and "ok?" in html_part
    assert second["To"] == "admins@rbaclens.test"
    with pytest.raises(Undeliverable):
        sender.send(_n(recipient="ghost-id"))


def test_smtp_channel_from_settings(monkeypatch):
    from app.core.config import settings
    from app.core.notify_email import SmtpNotifier

    monkeypatch.setattr(settings, "notification_channel", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    assert notify.notifier() is None
    monkeypatch.setattr(settings, "smtp_host", "mailpit")
    monkeypatch.setattr(settings, "smtp_port", 1025)
    monkeypatch.setattr(settings, "notification_sender", "")
    s = notify.notifier()
    assert isinstance(s, SmtpNotifier) and (s.host, s.port, s.sender, s.channel) == (
        "mailpit",
        1025,
        "rbaclens@localhost",
        "smtp",
    )


def test_graph_unreachable_is_retried_not_closed(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    def unreachable(_oid):
        raise RuntimeError("Graph unreachable")

    sender = EmailNotifier(Cred(), "s@example.org", address_of=unreachable, post=lambda *_a: Resp())
    with SessionLocal() as db:
        _outbox(db, _n(recipient="alice-id"))
        assert deliver_pending(db, sender) == 0
        row = db.query(Notification).one()
        assert row.sent_at is None and row.error == "RuntimeError: Graph unreachable"
