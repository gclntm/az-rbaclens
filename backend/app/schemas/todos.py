"""Payloads of the to-do endpoints (core/todos.py)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TodoStatus = Literal["open", "done", "dismissed"]


class TodoRead(BaseModel):
    id: int
    round_id: int
    period: str | None
    item_id: int
    grant_id: str
    action: Literal["remove", "change"]
    scope: str
    scope_name: str | None
    principal_id: str
    principal_name: str | None
    principal_type: str | None
    role_name: str | None
    note: str | None
    advice_rule: str | None
    how: list[str]
    commands: list[str]
    reviewer_name: str | None
    decided_by_name: str | None
    created_at: datetime
    due_at: datetime
    status: TodoStatus
    overdue: bool
    done_at: datetime | None
    dismissed_reason: str | None
    dismissed_by_name: str | None
    # Change: grants the same identity now holds on the same place (the replacement, if made).
    replacements: list[str] = Field(default_factory=list)
    can_dismiss: bool = False


class TodoSummary(BaseModel):
    open: int
    overdue: int
    mine_open: int
    mine_overdue: int


class TodoDismiss(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
