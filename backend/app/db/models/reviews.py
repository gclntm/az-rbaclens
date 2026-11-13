"""Who is responsible, monthly reviews, to-dos, saved assessments, AI texts and notifications."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class Responsibility(Base):
    """The person responsible for a place, set in RBAC Lens by an admin.

    Applies to everything inside the place unless someone else is set
    lower down. Not an Azure role: it gives no access, it only decides
    who reviews the place.
    """

    __tablename__ = "responsibilities"

    scope_id: Mapped[str] = mapped_column(
        ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    person_name: Mapped[str | None] = mapped_column(String(500))
    person_email: Mapped[str | None] = mapped_column(String(320))
    # manual | tag (accepted from a tag suggestion)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    set_by: Mapped[str | None] = mapped_column(String(255))
    set_by_name: Mapped[str | None] = mapped_column(String(500))
    set_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("source IN ('manual', 'tag')", name="ck_responsibilities_source"),
    )


class ResponsibleSuggestion(Base):
    """A place whose responsible-person tag (``owner`` by default) was read.

    ``open``: the tag holds the email of one Entra ID user; ``unmatched``:
    it does not (not an email, or nobody has it); ``ignored`` / ``used``:
    an admin decided. Refreshed after each check and when the tag name
    changes; a decision sticks while the tag value stays the same.
    """

    __tablename__ = "responsible_suggestions"

    scope_id: Mapped[str] = mapped_column(
        ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_value: Mapped[str] = mapped_column(Text, nullable=False)
    person_id: Mapped[str | None] = mapped_column(String(64))
    person_name: Mapped[str | None] = mapped_column(String(500))
    person_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'unmatched', 'ignored', 'used')",
            name="ck_responsible_suggestions_status",
        ),
    )


class ReviewRound(Base):
    """One month's review of one place by its responsible person.

    ``scope_id`` is the place where the responsible person is set; the
    round covers grants given there and inside it, except parts where
    someone else is set lower down. ``scope_id`` NULL is the admins'
    round: places nobody is responsible for, and items that would
    otherwise be someone's own access.
    """

    __tablename__ = "review_rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[str | None] = mapped_column(
        ForeignKey("scopes.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM, container local time
    reviewer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(500))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_by_name: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        Index("uq_review_rounds_scope_period", "scope_id", "period", unique=True),
        Index(
            "uq_review_rounds_admins_period",
            "period",
            unique=True,
            sqlite_where=text("scope_id IS NULL"),
            postgresql_where=text("scope_id IS NULL"),
        ),
    )


class ReviewItem(Base):
    """One grant in a round, with the reviewer's decision.

    The grant is copied in (who, role, where) so a decided item still
    reads well after the grant is gone from Azure.
    """

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("review_rounds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    grant_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_name: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str | None] = mapped_column(String(50))
    role_definition_id: Mapped[str] = mapped_column(Text, nullable=False)
    role_name: Mapped[str | None] = mapped_column(String(255))
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    azure_id: Mapped[str | None] = mapped_column(Text)
    # Set when the item came to this round because the place's own
    # reviewer holds the access (no self-review).
    moved_from_scope: Mapped[str | None] = mapped_column(Text)
    # keep | remove | change | ask; NULL = still to decide.
    decision: Mapped[str | None] = mapped_column(String(10))
    # The playbook's advice (core/advice.py), refreshed at each check while
    # undecided: the decision it suggests, its rule and its title.
    suggested: Mapped[str | None] = mapped_column(String(10))
    suggested_rule: Mapped[str | None] = mapped_column(String(40))
    suggested_title: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_by_name: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Kept last month and unchanged since: starts as "keep".
    carried_from_round_id: Mapped[int | None] = mapped_column(Integer)
    ask_remind_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # First time a check no longer saw the grant (removed in Azure).
    gone_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("uq_review_items_round_grant", "round_id", "grant_fingerprint", unique=True),
        CheckConstraint(
            "decision IS NULL OR decision IN ('keep', 'remove', 'change', 'ask')",
            name="ck_review_items_decision",
        ),
    )


class Todo(Base):
    """A change to make in Azure, from a finished review: remove a grant, or
    change how it is given. The daily check closes it ("done") once it no
    longer sees the grant. The grant is copied in so it reads well after.
    """

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("review_rounds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("review_items.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    grant_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # remove | change
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_name: Mapped[str | None] = mapped_column(Text)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str | None] = mapped_column(String(50))
    role_name: Mapped[str | None] = mapped_column(String(255))
    # The reviewer's note, and the advice's steps and commands at finish time.
    note: Mapped[str | None] = mapped_column(Text)
    advice_rule: Mapped[str | None] = mapped_column(String(40))
    how: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    commands: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(500))
    decided_by_name: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", index=True)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dismissed_reason: Mapped[str | None] = mapped_column(Text)
    dismissed_by_name: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint("action IN ('remove', 'change')", name="ck_todos_action"),
        CheckConstraint("status IN ('open', 'done', 'dismissed')", name="ck_todos_status"),
    )


class Assessment(Base):
    """A saved assessment report: its control statuses and key numbers at one
    moment, read-only, so later reports can say what changed since."""

    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[str | None] = mapped_column(Text, index=True)  # NULL = the whole tenant
    scope_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_by_name: Mapped[str | None] = mapped_column(String(500))
    access_as_of: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)
    statuses: Mapped[dict[str, str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    numbers: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )


class AiText(Base):
    """A cached AI wording (core/ai_wording.py), keyed by a hash of the model,
    the prompt version and the facts sent: same facts, same text, no new call."""

    __tablename__ = "ai_texts"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Notification(Base):
    """A message RBAC Lens would send: a question (Ask), a review opened or
    due soon, or a reminder for an open question.

    Rows wait here (``sent_at`` NULL) until a channel (email, Teams) is
    connected; ``dedupe_key`` makes every writer idempotent.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ask | review_opened | review_due | reminder | todo_overdue | data_stale
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_id: Mapped[str | None] = mapped_column(String(64), index=True)
    recipient_name: Mapped[str | None] = mapped_column(String(500))
    # What it is about, e.g. "review:3" or "review:3/item:41".
    subject_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    channel: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('ask', 'review_opened', 'review_due', 'reminder', 'todo_overdue', 'data_stale')",
            name="ck_notifications_kind",
        ),
    )
