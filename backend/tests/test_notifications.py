"""Notification outbox: who gets what, once; nothing sent without a channel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.test_reviews import NOV, SUB_SCOPE, _open, _rounds, _world


def _outbox():
    from app.db.models import Notification
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        rows = db.query(Notification).order_by(Notification.id).all()
        db.expunge_all()
        return rows


def test_opening_a_review_tells_its_reviewer_once(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    _open(NOV)
    opened = [(n.kind, n.recipient_name) for n in _outbox()]
    assert sorted(opened) == [("review_opened", "Admins"), ("review_opened", "Marc Dubois")]
    assert all(n.sent_at is None for n in _outbox())


def test_ask_goes_to_the_person_whose_access_it_is(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    rid = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    items = {
        i["principal_id"]: i for i in app_with_sqlite.get(f"/api/reviews/{rid}").json()["items"]
    }
    app_with_sqlite.put(
        f"/api/reviews/{rid}/items/{items['alice-id']['id']}",
        json={"decision": "ask", "note": "Still needed?"},
    )
    app_with_sqlite.put(
        f"/api/reviews/{rid}/items/{items['grp-1']['id']}", json={"decision": "ask"}
    )
    asks = [n for n in _outbox() if n.kind == "ask"]
    assert [(n.recipient_id, n.payload["to_the_person"]) for n in asks] == [
        ("alice-id", True),
        ("marc-id", False),
    ]
    assert (
        asks[0].payload["note"] == "Still needed?" and asks[0].payload["path"] == f"/reviews/{rid}"
    )


def test_due_soon_and_reminders(app_with_sqlite: TestClient):
    from app.core.reviews import open_and_sync
    from app.db.models import ReviewItem
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    _open(NOV)
    rid = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    item = app_with_sqlite.get(f"/api/reviews/{rid}").json()["items"][0]
    app_with_sqlite.put(f"/api/reviews/{rid}/items/{item['id']}", json={"decision": "ask"})
    with SessionLocal() as db:
        db.get(ReviewItem, item["id"]).ask_remind_at = NOV  # reminder date reached
        db.commit()
    late = datetime(2026, 11, 28, 12, tzinfo=UTC)
    with SessionLocal() as db:
        stats = open_and_sync(db, late)
    assert stats["notifications"] == 3  # due soon for both rounds, one reminder
    with SessionLocal() as db:
        assert open_and_sync(db, late)["notifications"] == 0  # once
        assert db.get(ReviewItem, item["id"]).ask_remind_at.replace(tzinfo=UTC) == late + timedelta(
            days=7
        )
    kinds = sorted(n.kind for n in _outbox())
    assert kinds.count("review_due") == 2 and kinds.count("reminder") == 1


def test_status_says_not_connected(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    s = app_with_sqlite.get("/api/notifications/status").json()
    assert (s["connected"], s["channel"], s["pending"], s["sent"]) == (False, None, 2, 0)
    assert s["by_kind"] == {"review_opened": 2}
    assert len(app_with_sqlite.get("/api/notifications").json()) == 2


def test_deliver_pending_with_a_channel():
    from app.core.notify import deliver_pending

    class Fake:
        channel = "test"

        def __init__(self):
            self.sent = []

        def send(self, n):
            if n.kind == "reminder":
                raise RuntimeError("boom")
            self.sent.append(n.id)

    class Row:
        def __init__(self, i, kind):
            self.id, self.kind, self.sent_at, self.channel, self.error = i, kind, None, None, None
            self.created_at = datetime.now(UTC)

    rows = [Row(1, "ask"), Row(2, "reminder")]

    class Session:
        def scalars(self, _stmt):
            class R:
                def all(self_inner):
                    return rows

            return R()

        def commit(self):
            pass

    from app.core import notify

    notify._paused_until = None
    fake = Fake()
    assert deliver_pending(Session(), fake) == 1
    assert (rows[0].channel, rows[1].error) == ("test", "RuntimeError: boom")
    notify._paused_until = None


def test_every_kind_can_be_listed(app_with_sqlite: TestClient):
    """The list once failed with a 500 on a "data stale" message: the API knew only four kinds."""
    from app.core.notify import KINDS, enqueue
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        for kind in KINDS:
            enqueue(
                db,
                kind,
                recipient_id=None,
                recipient_name="Admins",
                subject_ref=f"t:{kind}",
                payload={},
                dedupe_key=f"t:{kind}",
            )
        db.commit()
    r = app_with_sqlite.get("/api/notifications")
    assert r.status_code == 200
    assert {n["kind"] for n in r.json()} == set(KINDS)
