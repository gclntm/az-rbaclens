"""Times are UTC and timezone-aware everywhere in RBAC Lens.

Database columns use ``app.db.types.UTCDateTime``, which hands back aware UTC
values on every engine. ``as_utc`` is for times that come from outside: a query
parameter, a request body, an Azure SDK object. A naive time is taken as UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import overload


@overload
def as_utc(dt: datetime) -> datetime: ...
@overload
def as_utc(dt: None) -> None: ...
def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
