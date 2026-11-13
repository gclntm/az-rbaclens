"""Column types shared by the models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.timeutil import as_utc


class UTCDateTime(TypeDecorator[datetime]):
    """``timestamptz`` that always reads back as an aware UTC datetime.

    Postgres already returns aware values (in the session's time zone, made UTC
    here); SQLite, used by the tests, returns naive ones, taken as UTC. A naive
    value written is taken as UTC too. So no code needs to "make a time aware".
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        return as_utc(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return as_utc(value)
