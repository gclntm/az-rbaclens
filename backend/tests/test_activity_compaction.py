"""Machine activity as daily counts (core/activity_compaction.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import activity_compaction as ac

SUB = "aaaaaaaa-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 1)  # nine days before NOW: old enough
ARC = "Microsoft.AzureArcData/sqlServerInstances/databases/write"
DB = f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.AzureArcData/sqlServerInstances/sql1/databases/db1"
RA_WRITE = "Microsoft.Authorization/roleAssignments/write"


@pytest.mark.parametrize(
    ("caller", "ctype", "op", "status", "full"),
    [
        ("ada@example.org", "User", ARC, "Succeeded", True),  # a person
        ("ada@example.org", None, ARC, "Succeeded", True),  # a person, by the caller's shape
        ("app-id", "ServicePrincipal", ARC, "Failed", True),  # a failure
        ("app-id", "ServicePrincipal", RA_WRITE, "Succeeded", True),  # a change to access
        ("app-id", "ServicePrincipal", "Microsoft.Authorization/locks/delete", "Started", True),
        (
            "app-id",
            "ServicePrincipal",
            "Microsoft.Management/managementGroups/subscriptions/write",
            "Succeeded",
            True,
        ),
        (
            "app-id",
            "ServicePrincipal",
            "Microsoft.Authorization/elevateAccess/action",
            "Succeeded",
            True,
        ),
        ("app-id", "ServicePrincipal", ARC, "Succeeded", False),  # routine machine activity
        (
            "app-id",
            "ServicePrincipal",
            "Microsoft.Authorization/policies/audit/action",
            "Succeeded",
            False,
        ),
        (None, None, "Microsoft.Resourcehealth/healthevent/Updated/action", "Updated", False),
    ],
)
def test_what_is_kept_in_full(caller, ctype, op, status, full):
    assert ac.keep_in_full(caller, ctype, op, status) is full


def _ev(
    n: int,
    *,
    at: datetime,
    op: str = ARC,
    status: str = "Succeeded",
    caller: str = "app-id",
    ctype: str | None = "ServicePrincipal",
    oid: str | None = "app-oid",
    corr: str | None = None,
    resource: str = DB,
    target: str = SUB,
):
    from app.db.models import ActivityEvent

    return ActivityEvent(
        subscription_id=target,
        event_data_id=f"ev-{n}",
        caller=caller,
        caller_type=ctype,
        caller_oid=oid,
        operation_name=op,
        resource_id=resource,
        resource_type="Microsoft.AzureArcData/sqlServerInstances/databases",
        status=status,
        correlation_id=corr or f"corr-{n}",
        occurred_at=at,
        raw={"big": "x" * 50},
    )


def _seed_day(db) -> None:
    """On DAY: an app writes a database 5 times (each Started then Succeeded), once in
    another letter case; a person does one thing; an app changes access; an app fails;
    one Started has no outcome; a day later, the same app again."""
    t0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    rows = []
    for i in range(5):
        at = t0 + timedelta(hours=i)
        resource = DB.upper() if i == 4 else DB
        rows.append(_ev(10 + i, at=at, status="Started", corr=f"c{i}", resource=resource))
        rows.append(_ev(20 + i, at=at + timedelta(seconds=5), corr=f"c{i}", resource=resource))
    rows += [
        _ev(30, at=t0, caller="ada@example.org", ctype="User", oid="ada-oid"),
        _ev(31, at=t0, op=RA_WRITE, status="Started", corr="ra"),
        _ev(32, at=t0 + timedelta(seconds=3), op=RA_WRITE, corr="ra"),
        _ev(33, at=t0, status="Failed"),
        _ev(34, at=t0, status="Started", corr="lonely"),
        _ev(40, at=t0 + timedelta(days=1)),  # the next day: its own row
    ]
    db.add_all(rows)
    db.commit()


def test_a_day_is_folded_into_daily_counts(app_with_sqlite: TestClient):
    from app.db.models import ActivityCompactedDay, ActivityEvent
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_day(db)
        before, after = ac.compact_day(db, SUB, DAY, NOW)
        rows = {r.event_data_id: r for r in db.query(ActivityEvent).all()}
        assert before == 15  # the day's rows; the next day's event is not touched
        kept = {k for k in rows if not k.startswith(ac.DAILY_PREFIX)}
        # Person, access change (its Started dropped), failure, and the next day's event.
        assert kept == {"ev-30", "ev-32", "ev-33", "ev-40"}
        daily = [r for r in rows.values() if r.event_data_id.startswith(ac.DAILY_PREFIX)]
        by_status = {r.status: r for r in daily}
        assert set(by_status) == {"Succeeded", "Started"}
        writes = by_status["Succeeded"]
        assert (writes.event_count, writes.caller_oid, writes.raw, writes.correlation_id) == (
            5,
            "app-oid",
            None,
            None,
        )
        first, last = writes.first_occurred_at, writes.occurred_at
        assert (first.hour, last.hour) == (8, 12)
        assert by_status["Started"].event_count == 1  # a Started without outcome is still counted
        assert after == len(rows) - 1  # every row of the day after folding
        marker = db.get(ActivityCompactedDay, (SUB, DAY))
        assert (marker.events_before, marker.rows_after) == (15, after)


def test_only_days_old_enough_are_folded_and_only_once(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_day(db)
        db.add(_ev(50, at=NOW - timedelta(days=2)))  # recent: a collection may still read it
        db.commit()
        result = ac.compact(db, NOW)
        assert result.days == 2  # DAY and the day after
        assert db.query(ActivityEvent).filter_by(event_data_id="ev-50").count() == 1
        assert ac.compact(db, NOW).days == 0


def test_counts_read_the_same_before_and_after(app_with_sqlite: TestClient):
    """What the person page and "used in 90 days" count does not change when a day is folded."""
    from app.core.scan_coverage import _aggregated_events
    from app.db.session import SessionLocal

    def usage(db):
        return sorted(
            (r[3], r[4], r[5], r[6])
            for r in _aggregated_events(db, {SUB}, NOW - timedelta(days=30), NOW)
            if r[4] == ARC
        )

    with SessionLocal() as db:
        _seed_day(db)
        old = usage(db)
        ac.compact(db, NOW)
        new = usage(db)
    # Only the five Started rows whose outcome is stored are gone; the rest counts the same,
    # per caller and resource (one resource, written in two letter cases).
    assert sum(n for *_, n in old) == 14 and sum(n for *_, n in new) == 9

    def per_caller(rows):
        out: dict[str, int] = {}
        for oid, _op, _res, n in rows:
            out[oid] = out.get(oid, 0) + n
        return out

    assert per_caller(old) == {"app-oid": 13, "ada-oid": 1}
    assert per_caller(new) == {"app-oid": 8, "ada-oid": 1}
    assert {res for _oid, _op, res, _n in new} == {DB.lower()}


def test_a_collection_does_not_count_a_folded_day_twice(app_with_sqlite: TestClient):
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal
    from app.tools.activity import _persist_entries

    def entry(n, *, op=ARC, status="Succeeded", caller="app-id", at):
        return SimpleNamespace(
            event_data_id=f"ev-{n}",
            caller=caller,
            operation_name=SimpleNamespace(value=op, localized_value=op),
            status=SimpleNamespace(value=status, localized_value=status),
            resource_id=DB,
            resource_type=SimpleNamespace(value="x", localized_value="x"),
            event_timestamp=at,
            correlation_id=f"c{n}",
            claims=None,
            as_dict=dict,
        )

    t0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    with SessionLocal() as db:
        _seed_day(db)
        ac.compact(db, NOW)
        rows_before = db.query(ActivityEvent).count()
        pull = ActivityPull(
            subscription_id=SUB,
            start_time=t0 - timedelta(hours=1),
            end_time=t0 + timedelta(days=1),
            status="running",
        )
        db.add(pull)
        db.commit()
        entries = [
            entry(20, at=t0),  # folded: skipped
            entry(10, status="Started", at=t0),  # a dropped Started: skipped
            entry(32, op=RA_WRITE, at=t0),  # kept in full: already stored
            entry(60, at=t0),  # Azure delivered it late, but the day is counted: skipped
            entry(61, caller="bob@example.org", at=t0),  # a person, new: stored
            entry(62, at=NOW - timedelta(days=1)),  # a day not folded: stored
        ]
        persisted, seen = _persist_entries(db, pull, iter(entries))
        assert (persisted, seen) == (2, 6)
        assert db.query(ActivityEvent).count() == rows_before + 2


def test_retention_folds_and_the_api_shows_the_count(app_with_sqlite: TestClient):
    from app.core.retention import run_retention
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_day(db)
        result = run_retention(db, NOW)
    assert (result.days_compacted, result.events_compacted) == (2, 16)
    events = app_with_sqlite.get("/api/activity/events", params={"subscription_id": SUB}).json()
    daily = max(
        (e for e in events if e["event_data_id"].startswith(ac.DAILY_PREFIX)),
        key=lambda e: e["event_count"],
    )
    assert daily["event_count"] == 5 and daily["first_occurred_at"] is not None
    person = next(e for e in events if e["event_data_id"] == "ev-30")
    assert person["event_count"] == 1 and person["first_occurred_at"] is None
    detail = app_with_sqlite.get(f"/api/activity/events/{daily['id']}").json()
    assert detail["raw"] is None
    csv = app_with_sqlite.get(
        "/api/activity/events/export", params={"fmt": "csv", "subscription_id": SUB}
    ).text
    assert csv.splitlines()[0].endswith("event_count,first_occurred_at")
