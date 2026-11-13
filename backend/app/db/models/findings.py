"""What needs a look: a check's coverage, its findings and the decisions on them."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class ScanCoverage(Base):
    """Cached ``RoleCoverageResult`` for a completed scan.

    Written by ``compute_scan_findings`` at snapshot end (and on
    recompute). ``GET /scans/{id}/coverage`` serves this when present
    instead of re-joining role definitions, memberships and activity
    events on every request.
    """

    __tablename__ = "scan_coverage"

    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # The previous completed scan of the same root that ``drift_added``
    # findings were computed against; null when there was none.
    previous_scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )


class Finding(Base):
    """One reviewable statement about one role assignment in one scan.

    Replaced wholesale on every recompute of the scan. The durable
    identity is ``fingerprint`` (``sha256(scope|role GUID|principal|
    category)``), which ``review_decisions`` keys on — so a decision
    made on snapshot #41 still applies to the same finding in #42.
    """

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("role_assignments.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # One of app.core.findings.CATEGORIES.
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # high|medium|low
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("uq_findings_scan_fingerprint", "scan_id", "fingerprint", unique=True),
        Index("ix_findings_scan_severity", "scan_id", "severity"),
    )


class ReviewDecision(Base):
    """A reviewer's verdict on a finding fingerprint.

    Keyed by fingerprint, not finding id (see ``Finding``). ``snoozed``
    decisions expire: the API reports the finding as ``open`` again once
    ``snoozed_until`` has passed, without deleting the row.
    """

    __tablename__ = "review_decisions"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    # reviewed | accepted_risk | snoozed | resolved
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    actor: Mapped[str | None] = mapped_column(String(255))
    actor_name: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
