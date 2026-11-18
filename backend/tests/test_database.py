"""Tests for the /api/settings/database endpoints.

The fixture uses an in-memory SQLite, so we exercise the dialect-aware
fallbacks: stats returns table list + row counts (no size info), and
the maintenance routes run their SQLite-equivalent statements.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_stats_returns_table_list_on_sqlite(app_with_sqlite: TestClient):
    body = app_with_sqlite.get("/api/settings/database/stats").json()

    assert body["dialect"] == "sqlite"
    assert body["database_size_bytes"] is None  # not available on SQLite
    table_names = {t["name"] for t in body["tables"]}
    # Core IAM-access tables exist after fixture's create_all.
    assert {"scans", "role_assignments", "azure_credentials"} <= table_names

    # Each table reports a row_count (0 for empty fixtures).
    for t in body["tables"]:
        assert t["row_count"] is not None
        assert t["total_size_bytes"] is None  # only populated on Postgres


def test_stats_row_counts_reflect_data(app_with_sqlite: TestClient):
    """After inserting two scans the stats endpoint should report 2 rows."""
    app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-a"})
    app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-b"})

    body = app_with_sqlite.get("/api/settings/database/stats").json()
    scans_row = next(t for t in body["tables"] if t["name"] == "scans")
    assert scans_row["row_count"] == 2


def test_vacuum_runs_on_sqlite(app_with_sqlite: TestClient):
    response = app_with_sqlite.post("/api/settings/database/vacuum")
    assert response.status_code == 200
    body = response.json()
    assert body["operation"] == "vacuum"
    assert body["duration_ms"] >= 0


def test_analyze_runs_on_sqlite(app_with_sqlite: TestClient):
    response = app_with_sqlite.post("/api/settings/database/analyze")
    assert response.status_code == 200
    assert response.json()["operation"] == "analyze"


def test_reindex_runs_on_sqlite(app_with_sqlite: TestClient):
    response = app_with_sqlite.post("/api/settings/database/reindex")
    assert response.status_code == 200
    assert response.json()["operation"] == "reindex"
