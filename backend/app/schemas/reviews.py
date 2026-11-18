"""Payloads of the monthly review endpoints."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.capabilities import CapabilityLevel
from app.schemas.iam_access import ScopeLevel

Decision = Literal["keep", "remove", "change", "ask"]


class ReviewProgress(BaseModel):
    total: int
    keep: int
    remove: int
    change: int
    ask: int
    open: int
    # Grants removed in Azure before anyone decided.
    gone: int


class ReviewRoundRead(BaseModel):
    id: int
    # Null for the admins' round.
    scope_id: str | None
    scope_name: str | None
    scope_level: ScopeLevel | None
    period: str
    reviewer_id: str | None
    reviewer_name: str | None
    opened_at: datetime
    due_at: datetime
    finished_at: datetime | None
    finished_by_name: str | None
    progress: ReviewProgress
    # Undecided or still asked, grant still there: these block "Finish".
    blocking: int
    can_decide: bool


class ReviewItemRead(BaseModel):
    id: int
    # grant | owner | secret: an owner of an app or group, or an app's secret,
    # reviewed like a grant; ``grant_id`` is then the strongest grant it reaches.
    kind: str = "grant"
    # Owner: object_id, object_name, object_kind, via, owner_id, owner_name, app_id.
    # Secret: object_id, object_name, via, kind, key_id, start_at, end_at, app_id.
    detail: dict[str, Any] | None = None
    grant_id: str
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    principal_id: str
    principal_name: str | None
    principal_type: str | None
    role_definition_id: str
    role_name: str | None
    level: CapabilityLevel
    # The place whose reviewer holds this access (it moved here: no self-review).
    moved_from_scope: str | None
    moved_from_name: str | None
    decision: Decision | None
    # The playbook's advice while undecided (core/advice.py): the decision it
    # suggests, its rule and title.
    suggested: Decision | None = None
    suggested_rule: str | None = None
    suggested_title: str | None = None
    note: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    carried: bool
    ask_remind_at: datetime | None
    gone_at: datetime | None
    # Ready-to-run Azure CLI command (shown for "remove"); RBAC Lens never runs it.
    remove_command: str


class ReviewRoundDetail(ReviewRoundRead):
    items: list[ReviewItemRead]


class ReviewDecisionWrite(BaseModel):
    decision: Decision | None
    note: str | None = Field(default=None, max_length=2000)


class ReviewRef(BaseModel):
    """The open review item for a grant, shown on the Grant page."""

    round_id: int
    item_id: int
    period: str
    reviewer_name: str | None
    decision: Decision | None
    can_decide: bool


class AcceptAdviceResult(BaseModel):
    """How many suggested decisions were confirmed, by decision."""

    keep: int
    remove: int
    change: int
    ask: int
