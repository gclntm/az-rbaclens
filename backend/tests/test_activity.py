"""Tests for the Activity Log API and puller.

The Azure Monitor SDK call is monkey-patched out (see conftest), so
these exercise:
- the route layer (validation, filters, idempotent re-pulls)
- the puller's normalization + dedupe logic when given fake SDK
  ``EventData`` shaped objects
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

SUB_ID = "00000000-0000-0000-0000-000000000001"


# --------------------------------------------------------------------------------------
# Route validation
# --------------------------------------------------------------------------------------


def test_start_pull_rejects_inverted_window(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    r = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": now.isoformat(),
            "end": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert r.status_code == 400


def test_start_pull_rejects_window_over_90_days(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    r = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": (now - timedelta(days=120)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert r.status_code == 400


def test_start_pull_creates_pending_row(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    r = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": (now - timedelta(days=7)).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["subscription_id"] == SUB_ID
    assert body["status"] == "pending"
    assert body["events_persisted"] == 0


def _started_at(body: dict) -> datetime:
    start = datetime.fromisoformat(body["start_time"])
    return start if start.tzinfo else start.replace(tzinfo=UTC)


def test_start_pull_caps_start_inside_retention(app_with_sqlite: TestClient):
    """The form's "90 days" sends a start at exactly 90 days back, which
    Azure refuses once the pull has paged a while: it is moved up to
    89 days back."""
    now = datetime.now(UTC)
    window = {"start": (now - timedelta(days=90)).isoformat(), "end": now.isoformat()}

    r = app_with_sqlite.post("/api/activity/pulls", json={"subscription_id": SUB_ID, **window})
    assert r.status_code == 201
    start = _started_at(r.json())
    assert now - timedelta(days=89) <= start <= datetime.now(UTC) - timedelta(days=89)


def test_start_pull_rejects_window_before_retention(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    r = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": (now - timedelta(days=95)).isoformat(),
            "end": (now - timedelta(days=89, hours=1)).isoformat(),
        },
    )
    assert r.status_code == 400
    assert "what Azure keeps" in r.json()["detail"]


def test_list_pulls_filters_by_subscription(app_with_sqlite: TestClient):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    other_sub = "00000000-0000-0000-0000-000000000002"
    for sub in (SUB_ID, other_sub, SUB_ID):
        r = app_with_sqlite.post(
            "/api/activity/pulls",
            json={
                "subscription_id": sub,
                "start": (now - timedelta(days=1)).isoformat(),
                "end": now.isoformat(),
            },
        )
        assert r.status_code == 201
        # Only one in-flight pull per subscription is allowed; finish
        # each one so the next POST for the same sub is accepted.
        with SessionLocal() as db:
            db.get(ActivityPull, r.json()["id"]).status = "done"
            db.commit()

    rows = app_with_sqlite.get(f"/api/activity/pulls?subscription_id={SUB_ID}").json()
    assert len(rows) == 2
    assert all(r["subscription_id"] == SUB_ID for r in rows)


def test_delete_pull_keeps_events(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    # Seed a pull row + one event for the same subscription, then
    # delete the pull and confirm the event is still there.
    now = datetime.now(UTC)
    created = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": (now - timedelta(days=1)).isoformat(),
            "end": now.isoformat(),
        },
    ).json()
    pull_id = created["id"]

    with SessionLocal() as db:
        db.add(
            ActivityEvent(
                subscription_id=SUB_ID,
                event_data_id="ev-1",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/write",
                status="Succeeded",
                occurred_at=now,
            )
        )
        db.commit()

    r = app_with_sqlite.delete(f"/api/activity/pulls/{pull_id}")
    assert r.status_code == 204

    events = app_with_sqlite.get("/api/activity/events").json()
    assert len(events) == 1
    assert events[0]["event_data_id"] == "ev-1"


# --------------------------------------------------------------------------------------
# Event query filters
# --------------------------------------------------------------------------------------


def _seed_events(db, *, n_alice=2, n_bob=1):
    from app.db.models import ActivityEvent

    base = datetime.now(UTC) - timedelta(hours=1)
    for i in range(n_alice):
        db.add(
            ActivityEvent(
                subscription_id=SUB_ID,
                event_data_id=f"alice-{i}",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/write",
                status="Succeeded" if i == 0 else "Failed",
                occurred_at=base + timedelta(minutes=i),
            )
        )
    for i in range(n_bob):
        db.add(
            ActivityEvent(
                subscription_id=SUB_ID,
                event_data_id=f"bob-{i}",
                caller="bob@example.com",
                caller_type="User",
                operation_name="Microsoft.Compute/virtualMachines/start/action",
                status="Succeeded",
                occurred_at=base + timedelta(minutes=10 + i),
            )
        )
    db.commit()


def test_list_events_returns_newest_first(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db, n_alice=2, n_bob=1)

    rows = app_with_sqlite.get("/api/activity/events").json()
    assert len(rows) == 3
    times = [r["occurred_at"] for r in rows]
    assert times == sorted(times, reverse=True)


def test_list_events_filters_caller_substring(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    rows = app_with_sqlite.get("/api/activity/events?caller=alice").json()
    assert len(rows) == 2
    assert all("alice" in r["caller"] for r in rows)


def test_list_events_filters_operation_substring(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    rows = app_with_sqlite.get("/api/activity/events?operation_name=virtualMachines").json()
    assert len(rows) == 1
    assert rows[0]["caller"] == "bob@example.com"


def test_list_events_filters_status_exact(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    rows = app_with_sqlite.get("/api/activity/events?status=Failed").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "Failed"


def test_list_events_filters_time_window(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    # Filter to events newer than 55 minutes ago — should capture bob's
    # rows (placed at +10/+11 minutes from base = ~50 min ago) but not
    # Alice's (placed at minute 0/1 of base = ~1h ago).
    from urllib.parse import quote

    cutoff = (datetime.now(UTC) - timedelta(minutes=55)).isoformat()
    rows = app_with_sqlite.get(f"/api/activity/events?from={quote(cutoff)}").json()
    callers = {r["caller"] for r in rows}
    assert callers == {"bob@example.com"}


# --------------------------------------------------------------------------------------
# Puller normalization + dedupe
# --------------------------------------------------------------------------------------


def _fake_entry(
    *,
    event_data_id="evt-1",
    caller="alice@example.com",
    op="Microsoft.Storage/storageAccounts/write",
    status="Succeeded",
    when=None,
    claims=None,
):
    when = when or datetime.now(UTC)
    return SimpleNamespace(
        event_data_id=event_data_id,
        caller=caller,
        operation_name=SimpleNamespace(value=op, localized_value=op),
        status=SimpleNamespace(value=status, localized_value=status),
        resource_id=f"/subscriptions/{SUB_ID}/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa1",
        resource_type=SimpleNamespace(
            value="Microsoft.Storage/storageAccounts",
            localized_value="storage account",
        ),
        event_timestamp=when,
        correlation_id="corr-1",
        claims=claims,
        as_dict=lambda: {"event_data_id": event_data_id},
    )


def test_normalize_picks_value_over_localized():
    """``operation_name.value`` is the programmatic form we want for
    later coverage analysis — never the localized human label."""
    from app.tools.activity import _normalize

    entry = SimpleNamespace(
        event_data_id="x",
        caller="a@b",
        operation_name=SimpleNamespace(
            value="Microsoft.Storage/storageAccounts/write",
            localized_value="Écrit un compte de stockage",
        ),
        status=SimpleNamespace(value="Succeeded", localized_value="Succès"),
        resource_id="/sub/x",
        resource_type=SimpleNamespace(
            value="Microsoft.Storage/storageAccounts",
            localized_value="compte de stockage",
        ),
        event_timestamp=datetime.now(UTC),
        correlation_id="c",
        claims=None,
        as_dict=dict,
    )
    row = _normalize(entry, SUB_ID)
    assert row.operation_name == "Microsoft.Storage/storageAccounts/write"
    assert row.status == "Succeeded"
    assert row.resource_type == "Microsoft.Storage/storageAccounts"


def test_infer_caller_type_uses_idtyp_claim_when_present():
    from app.tools.activity import _infer_caller_type

    assert _infer_caller_type("anything", {"idtyp": "user"}) == "User"
    assert _infer_caller_type("anything", {"idtyp": "app"}) == "ServicePrincipal"


def test_infer_caller_type_upn_falls_back_to_user():
    from app.tools.activity import _infer_caller_type

    assert _infer_caller_type("alice@example.com", None) == "User"


def test_infer_caller_type_guid_falls_back_to_sp():
    from app.tools.activity import _infer_caller_type

    guid = "11111111-2222-3333-4444-555555555555"
    assert _infer_caller_type(guid, None) == "ServicePrincipal"


def test_infer_caller_type_unknown_returns_none():
    from app.tools.activity import _infer_caller_type

    assert _infer_caller_type(None, None) is None
    assert _infer_caller_type("some-arbitrary-string", None) is None


def test_get_event_returns_raw_payload(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        ev = ActivityEvent(
            subscription_id=SUB_ID,
            event_data_id="raw-1",
            caller="alice@example.com",
            caller_type="User",
            operation_name="Microsoft.Storage/storageAccounts/write",
            status="Succeeded",
            occurred_at=datetime.now(UTC),
            raw={"claims": {"objectidentifier": "alice-guid"}, "extra": True},
        )
        db.add(ev)
        db.commit()
        ev_id = ev.id

    body = app_with_sqlite.get(f"/api/activity/events/{ev_id}").json()
    assert body["event_data_id"] == "raw-1"
    assert body["raw"]["claims"]["objectidentifier"] == "alice-guid"


def test_get_event_404(app_with_sqlite: TestClient):
    r = app_with_sqlite.get("/api/activity/events/9999")
    assert r.status_code == 404


def test_export_events_csv_returns_attachment(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    r = app_with_sqlite.get("/api/activity/events/export?fmt=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    # Default no-filter export uses ``-`` separator (not ``-filtered-``).
    assert "filtered" not in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    # Header + 3 events (2 alice + 1 bob).
    assert len(lines) == 4
    assert lines[0].startswith("occurred_at,")


def test_export_events_csv_marks_filtered_filename(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    r = app_with_sqlite.get("/api/activity/events/export?fmt=csv&caller=alice")
    assert r.status_code == 200
    assert "filtered" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert len(lines) == 3  # header + 2 alice rows


def test_export_events_json_includes_filters_block(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)

    r = app_with_sqlite.get("/api/activity/events/export?fmt=json&status=Failed")
    assert r.status_code == 200
    payload = r.json()
    assert payload["filters"]["status"] == "Failed"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["status"] == "Failed"


def test_collect_events_dedupes_by_event_data_id(app_with_sqlite: TestClient):
    """The puller must skip events whose event_data_id is already
    persisted for this subscription. Repeat pulls over overlapping
    windows are part of the design — they have to be safe."""
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as activity_mod

    # Phase 1: persist one event so the next pull will encounter a
    # collision on its event_data_id.
    with SessionLocal() as db:
        db.add(
            ActivityEvent(
                subscription_id=SUB_ID,
                event_data_id="evt-existing",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/write",
                status="Succeeded",
                occurred_at=datetime.now(UTC),
            )
        )
        db.commit()

    # Phase 2: hand the puller two entries — one duplicate of the
    # existing event, one new.
    pull = ActivityPull(
        subscription_id=SUB_ID,
        start_time=datetime.now(UTC) - timedelta(days=1),
        end_time=datetime.now(UTC),
        status="running",
    )

    class _FakeMonitor:
        class activity_logs:
            @staticmethod
            def list(*, filter):
                yield _fake_entry(event_data_id="evt-existing")
                yield _fake_entry(event_data_id="evt-new")

    with SessionLocal() as db:
        db.add(pull)
        db.commit()
        persisted, seen = activity_mod._collect_events(
            session=db, monitor=_FakeMonitor(), pull=pull
        )

    assert seen == 2
    assert persisted == 1
    with SessionLocal() as db:
        count = db.query(ActivityEvent).count()
        assert count == 2  # existing + the new one


def test_collect_events_beats_while_reading_duplicates(app_with_sqlite: TestClient, monkeypatch):
    """A re-pull over events already stored adds nothing, so no batch of
    new rows ever fills. The heartbeat must still move, or the stuck-job
    sweeper fails a pull whose worker is still paging."""
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as activity_mod

    monkeypatch.setattr(activity_mod, "HEARTBEAT_EVERY_ENTRIES", 10)
    monkeypatch.setattr(activity_mod, "HEARTBEAT_EVERY_SECONDS", 3600.0)

    with SessionLocal() as db:
        db.add_all(
            ActivityEvent(
                subscription_id=SUB_ID,
                event_data_id=f"evt-{i}",
                operation_name="Microsoft.Storage/storageAccounts/write",
                occurred_at=datetime.now(UTC),
            )
            for i in range(35)
        )
        db.commit()

    stale = datetime.now(UTC) - timedelta(hours=2)
    pull = ActivityPull(
        subscription_id=SUB_ID,
        start_time=datetime.now(UTC) - timedelta(days=1),
        end_time=datetime.now(UTC),
        status="running",
        heartbeat_at=stale,
    )

    class _FakeMonitor:
        class activity_logs:
            @staticmethod
            def list(*, filter):
                for i in range(35):
                    yield _fake_entry(event_data_id=f"evt-{i}")

    with SessionLocal() as db:
        db.add(pull)
        db.commit()
        beats: list[tuple[datetime, str]] = []
        real_commit = db.commit

        def recording_commit():
            beats.append((pull.heartbeat_at, pull.progress_message))
            real_commit()

        monkeypatch.setattr(db, "commit", recording_commit)
        persisted, seen = activity_mod._collect_events(
            session=db, monitor=_FakeMonitor(), pull=pull
        )

    assert (persisted, seen) == (0, 35)
    assert [msg for _, msg in beats] == [
        "Read 10 events, 0 new…",
        "Read 20 events, 0 new…",
        "Read 30 events, 0 new…",
    ]
    assert all(at is not None and at > stale for at, _ in beats)


def test_export_streams_in_chunks_and_handles_empty_results(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.api import activity as activity_api
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_events(db)
    monkeypatch.setattr(activity_api, "_EXPORT_CHUNK", 1)  # one row per chunk

    csv_lines = app_with_sqlite.get("/api/activity/events/export?fmt=csv").text.strip().splitlines()
    assert len(csv_lines) == 4 and csv_lines[0].startswith("occurred_at,subscription_id,caller")
    events = app_with_sqlite.get("/api/activity/events/export?fmt=json").json()["events"]
    assert len(events) == 3 and set(events[0]) >= {"occurred_at", "event_data_id", "operation_name"}
    assert "raw" not in events[0]

    empty = app_with_sqlite.get("/api/activity/events/export?fmt=json&caller=nobody").json()
    assert empty["events"] == [] and empty["filters"]["caller"] == "nobody"
    assert (
        app_with_sqlite.get("/api/activity/events/export?fmt=csv&caller=nobody")
        .text.strip()
        .count("\n")
        == 0
    )


# --------------------------------------------------------------------------------------
# Manual pulls: validated input
# --------------------------------------------------------------------------------------


def test_start_pull_validates_the_subscription_and_the_window(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    window = {"start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat()}

    not_a_guid = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": "prod-subscription", **window}
    )
    assert not_a_guid.status_code == 422

    # Stored lowercased: events, coverage and the one-at-a-time guard all key on that form.
    upper = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": SUB_ID.upper().replace("0", "A"), **window}
    )
    assert upper.status_code == 201 and upper.json()["subscription_id"] == SUB_ID.replace("0", "a")
    again = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": SUB_ID.replace("0", "a"), **window}
    )
    assert again.status_code == 409  # the same subscription, whatever the case

    future = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": now.isoformat(),
            "end": (now + timedelta(days=2)).isoformat(),
        },
    )
    assert future.status_code == 422 and "future" in future.text

    # A window without a time zone is read as UTC instead of failing with a 500.
    naive = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB_ID,
            "start": (now - timedelta(hours=3)).replace(tzinfo=None).isoformat(),
            "end": now.isoformat(),
        },
    )
    assert naive.status_code == 201


def test_collect_events_only_looks_for_duplicates_around_its_window(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as activity_mod

    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                ActivityEvent(
                    subscription_id=SUB_ID,
                    event_data_id="old",
                    operation_name="op",
                    occurred_at=now - timedelta(days=60),
                ),
                ActivityEvent(
                    subscription_id=SUB_ID,
                    event_data_id="recent",
                    operation_name="op",
                    occurred_at=now - timedelta(minutes=30),
                ),
            ]
        )
        pull = ActivityPull(
            subscription_id=SUB_ID,
            start_time=now - timedelta(hours=2),
            end_time=now,
            status="running",
        )
        db.add(pull)
        db.commit()

        class _FakeMonitor:
            class activity_logs:
                @staticmethod
                def list(*, filter):
                    yield _fake_entry(event_data_id="recent")
                    yield _fake_entry(event_data_id="new")

        ids = db.scalars(
            activity_mod._select_existing_event_ids(
                SUB_ID, pull.start_time - activity_mod.DEDUPE_SLACK
            )
        ).all()
        assert ids == ["recent"]  # not the 60-day-old one
        persisted, seen = activity_mod._collect_events(
            session=db, monitor=_FakeMonitor(), pull=pull
        )
    assert (persisted, seen) == (1, 2)


def test_findings_are_recomputed_once_for_a_round_of_pulls(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core import findings
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    calls: list[set[str]] = []
    monkeypatch.setattr(
        findings, "recompute_for_subscriptions", lambda _s, subs: calls.append(set(subs)) or [1]
    )
    monkeypatch.setattr(findings, "_dirty", set())
    monkeypatch.setattr(findings, "_dirty_since", None)

    now = datetime.now(UTC)
    with SessionLocal() as db:
        assert findings.flush_recompute(db, now) == []  # nothing new: nothing to do
        findings.request_recompute("SUB-A")
        findings.request_recompute("sub-b")
        running = ActivityPull(
            subscription_id=SUB_ID, start_time=now, end_time=now, status="running"
        )
        db.add(running)
        db.commit()
        assert findings.flush_recompute(db, now) == []  # a pull is still running: wait for it
        running.status = "done"
        db.commit()
        assert findings.flush_recompute(db, now) == [1]
        assert findings.flush_recompute(db, now) == []
    assert calls == [{"sub-a", "sub-b"}]

    # New events don't wait for ever behind pulls that keep running.
    with SessionLocal() as db:
        findings.request_recompute("sub-c")
        db.add(ActivityPull(subscription_id=SUB_ID, start_time=now, end_time=now, status="running"))
        db.commit()
        assert findings.flush_recompute(db, datetime.now(UTC) + findings.RECOMPUTE_MAX_WAIT) == [1]


def test_a_pull_with_nothing_new_does_not_recompute_findings(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core import findings
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as activity_mod

    recomputed: list[set[str]] = []
    monkeypatch.setattr(
        findings, "recompute_for_subscriptions", lambda _s, subs: recomputed.append(set(subs)) or []
    )
    monkeypatch.setattr(activity_mod, "get_credential", lambda: None)
    monkeypatch.setattr(activity_mod, "get_azure_transport", lambda: None)
    monkeypatch.setattr(activity_mod, "MonitorManagementClient", lambda *_a, **_k: None)
    results = iter([(0, 5), (3, 5)])
    monkeypatch.setattr(activity_mod, "_collect_events", lambda **_k: next(results))

    now = datetime.now(UTC)

    def run_one() -> None:
        with SessionLocal() as db:
            p = ActivityPull(
                subscription_id=SUB_ID,
                start_time=now - timedelta(hours=1),
                end_time=now,
                status="pending",
            )
            db.add(p)
            db.commit()
            pull_id = p.id
        activity_mod.run_pull(pull_id)

    run_one()
    assert recomputed == []  # 0 new events
    run_one()
    assert recomputed == [{SUB_ID}]  # 3 new events (no scheduler loop in tests: done at once)
