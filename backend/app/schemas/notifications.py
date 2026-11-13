"""Payloads of the notification outbox endpoints."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NotificationRead(BaseModel):
    id: int
    kind: Literal["ask", "review_opened", "review_due", "reminder", "todo_overdue", "data_stale"]
    recipient_id: str | None
    recipient_name: str | None
    subject_ref: str
    payload: dict[str, Any]
    created_at: datetime
    sent_at: datetime | None
    channel: str | None
    error: str | None


class NotificationStatus(BaseModel):
    connected: bool
    channel: str | None
    # Email: the mailbox messages come from, and where the admins' ones go.
    sender: str | None = None
    admin_email: str | None = None
    links: bool = False
    # Where captured messages can be read (Mailpit's web inbox), when set.
    inbox_url: str | None = None
    pending: int
    sent: int
    # Closed without being sent (no address, too old).
    skipped: int = 0
    failed_last: str | None = None
    by_kind: dict[str, int]


class TestMessage(BaseModel):
    to: str = Field(min_length=3, max_length=320)
