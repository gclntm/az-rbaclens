"""Spreadsheet-safe CSV exports (core/csv_safe.py)."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import func, update

from app.core.csv_safe import SafeDictWriter, safe_cell, safe_writer
from tests.test_registry import SUB_SCOPE
from tests.test_reviews import NOV, _open, _rounds, _world

RISKY = ("=SUM(1,2)", "+1", "-1", "@A1", "\tx", "\rx")


def test_formula_like_text_becomes_plain_text():
    for value in RISKY:
        assert safe_cell(value) == "'" + value
    for fine in ("alice@example.com", "prod", "", "1-2", "Owner"):
        assert safe_cell(fine) == fine
    assert safe_cell(-5) == -5 and safe_cell(None) is None  # only text is touched


def test_both_writers_apply_it():
    buf = io.StringIO()
    safe_writer(buf).writerow(["=1+1", "ok"])
    SafeDictWriter(buf, fieldnames=["a"]).writerow({"a": "@x"})
    assert buf.getvalue().splitlines() == ["'=1+1,ok", "'@x"]


def test_review_export_cells_are_spreadsheet_safe(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    with SessionLocal() as db:
        db.execute(
            update(RoleAssignment)
            .where(func.lower(RoleAssignment.principal_id) == "alice-id")
            .values(principal_name="=SUM(1,2)")
        )
        db.commit()
    _open(NOV)
    rid = _rounds(app_with_sqlite)[SUB_SCOPE]["id"]
    rows = list(csv.reader(io.StringIO(app_with_sqlite.get(f"/api/reviews/{rid}/export").text)))
    cells = [c for row in rows[1:] for c in row]
    assert "'=SUM(1,2)" in cells
    assert not any(c.startswith(("=", "+", "@")) for c in cells)
