"""Pydantic schemas for the database settings page."""

from datetime import datetime

from pydantic import BaseModel


class TableStats(BaseModel):
    name: str
    row_count: int | None
    total_size_bytes: int | None
    table_size_bytes: int | None
    index_size_bytes: int | None


class IndexStats(BaseModel):
    name: str
    table_name: str
    size_bytes: int | None
    is_unique: bool | None = None


class DatabaseStats(BaseModel):
    dialect: str  # "postgresql" | "sqlite"
    database_size_bytes: int | None
    tables: list[TableStats]
    indexes: list[IndexStats]
    fetched_at: datetime


class MaintenanceResult(BaseModel):
    operation: str  # vacuum | analyze | reindex | cleanup
    duration_ms: int
    detail: str
    rows_affected: int | None = None


class RetentionPolicy(BaseModel):
    """Current policy (from settings); ``0`` keeps everything."""

    interval_hours: int
    event_retention_days: int
    snapshot_keep_per_scope: int
    failed_snapshot_retention_days: int
    audit_retention_days: int
    change_retention_days: int = 365


class RetentionRun(BaseModel):
    duration_ms: int
    events_deleted: int
    snapshots_deleted: int
    failed_snapshots_deleted: int
    audit_deleted: int
    changes_deleted: int = 0
    pulls_deleted: int = 0
    # Machine activity folded into daily counts (core/activity_compaction.py).
    days_compacted: int = 0
    events_compacted: int = 0
    rows_after_compaction: int = 0


class CleanupRequest(BaseModel):
    older_than_days: int = 30
    only_failed: bool = False
