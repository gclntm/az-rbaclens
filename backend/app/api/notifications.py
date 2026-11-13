"""The notification outbox (admins): what RBAC Lens would send, and whether a channel is connected."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.notify import notifier
from app.db.models import Notification
from app.db.session import get_session
from app.schemas.notifications import NotificationRead, NotificationStatus, TestMessage
from app.tools.graph_resolver import looks_like_email

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/status", response_model=NotificationStatus)
def status(session: Annotated[Session, Depends(get_session)]) -> NotificationStatus:
    sender = notifier()

    def count(*where) -> int:
        return session.scalar(select(func.count()).select_from(Notification).where(*where)) or 0

    pending = count(Notification.sent_at.is_(None))
    skipped = count(Notification.sent_at.is_not(None), Notification.error.is_not(None))
    total = count()
    failed = session.scalar(
        select(Notification.error)
        .where(Notification.sent_at.is_(None), Notification.error.is_not(None))
        .order_by(Notification.id.desc())
        .limit(1)
    )
    by_kind = dict(
        session.execute(
            select(Notification.kind, func.count())
            .where(Notification.sent_at.is_(None))
            .group_by(Notification.kind)
        ).all()
    )
    return NotificationStatus(
        connected=sender is not None,
        channel=sender.channel if sender else None,
        sender=getattr(sender, "sender", None),
        admin_email=getattr(sender, "admin_email", None) or None,
        links=bool(getattr(sender, "app_url", "")),
        inbox_url=settings.notification_inbox_url.strip() or None if sender else None,
        pending=pending,
        sent=total - pending - skipped,
        skipped=skipped,
        failed_last=failed,
        by_kind=by_kind,
    )


@router.post("/test")
def send_test(payload: TestMessage) -> dict[str, str]:
    """Send one test email through the configured channel, now."""
    sender = notifier()
    if sender is None or not hasattr(sender, "send_to"):
        raise HTTPException(status_code=409, detail="email is not connected (NOTIFICATION_CHANNEL)")
    to = payload.to.strip()
    if not looks_like_email(to):
        raise HTTPException(status_code=400, detail="not an email address")
    try:
        sender.send_to(
            to,
            "RBAC Lens test message",
            '<div style="font-family:sans-serif"><p>This is a test message from RBAC Lens. '
            "Email notifications work.</p></div>",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}"[:400]) from exc
    return {"sent_to": to}


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[NotificationRead]:
    """Newest first."""
    rows = session.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()
    return [NotificationRead.model_validate(r, from_attributes=True) for r in rows]
