"""Spreadsheet-safe CSV writers for every export.

Names in exports come from Entra ID and Azure (people, groups, apps, roles,
places), and some of them are chosen by whoever creates the object. A
spreadsheet opening the CSV treats a cell that starts with ``=``, ``+``,
``-``, ``@``, a tab or a carriage return as a formula. Such text cells get a
leading apostrophe, so they show as plain text (the usual guidance, as in
OWASP's "CSV Injection" page). Numbers and empty cells are left alone.

Use :func:`safe_writer` / :class:`SafeDictWriter` instead of ``csv.writer`` /
``csv.DictWriter`` for anything a person downloads.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Text that a spreadsheet would read as a formula gets a leading apostrophe."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


class _SafeWriter:
    def __init__(self, f: Any, **kwargs: Any) -> None:
        self._w = csv.writer(f, **kwargs)

    def writerow(self, row: Iterable[Any]) -> Any:
        return self._w.writerow([safe_cell(v) for v in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)


def safe_writer(f: Any, **kwargs: Any) -> _SafeWriter:
    """Drop-in for ``csv.writer`` whose cells are spreadsheet-safe."""
    return _SafeWriter(f, **kwargs)


class SafeDictWriter(csv.DictWriter):
    """Drop-in for ``csv.DictWriter`` whose values are spreadsheet-safe."""

    def writerow(self, rowdict: Mapping[str, Any]) -> Any:
        return super().writerow({k: safe_cell(v) for k, v in rowdict.items()})

    def writerows(self, rowdicts: Iterable[Mapping[str, Any]]) -> None:
        for row in rowdicts:
            self.writerow(row)
