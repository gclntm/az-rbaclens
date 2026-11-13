"""Notifications: an outbox now, a channel later.

Writers call :func:`enqueue` (idempotent through ``dedupe_key``):

- ``ask``: a reviewer marked a grant "Ask"; to the person whose access it
  is (people only), otherwise to the reviewer.
- ``review_opened``: a monthly review opened; to its reviewer.
- ``review_due``: a review still open three days before it is due.
- ``reminder``: a question still open on its reminder date.
- ``todo_overdue``: review changes still open after 14 days.
- ``data_stale``: the data stopped being kept up to date; to the admins,
  once a day (``app.core.staleness``).

Nothing is sent while no channel is configured. ``NOTIFICATION_CHANNEL=email``
(with ``NOTIFICATION_SENDER``) sends through Microsoft Graph; ``smtp`` (with
``SMTP_HOST``) through plain SMTP, e.g. Mailpit (``app.core.notify_email``).
The scheduler delivers waiting rows every minute. A row that cannot be delivered (no address) is closed with the
reason; a failure (Graph refused) pauses delivery for 15 minutes and is
retried. Rows older than ``notification_max_age_days`` when a channel is
connected are closed unsent rather than sent late.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import as_utc
from app.db.models import Notification

logger = logging.getLogger(__name__)

KINDS = ("ask", "review_opened", "review_due", "reminder", "todo_overdue", "data_stale")
PAUSE_AFTER_FAILURE = timedelta(minutes=15)

_paused_until: datetime | None = None


class Undeliverable(Exception):
    """This notification can never be sent (e.g. no address): close it, don't retry."""


class Notifier(Protocol):
    """Delivers one notification; raises on failure."""

    channel: str

    def send(self, notification: Notification) -> None: ...


class NullNotifier:
    """No channel connected: nothing is sent."""

    channel = "none"

    def send(self, notification: Notification) -> None:  # pragma: no cover - never called
        raise RuntimeError("no notification channel is connected")


def notifier() -> Notifier | None:
    """The configured channel, or None while nothing is connected."""
    channel = settings.notification_channel.strip().lower()
    if not channel:
        return None
    if channel == "email":
        if not settings.notification_sender:
            logger.warning("NOTIFICATION_CHANNEL=email needs NOTIFICATION_SENDER; nothing is sent")
            return None
        from app.core.azure import get_credential
        from app.core.notify_email import EmailNotifier

        return EmailNotifier(
            get_credential(),
            settings.notification_sender.strip(),
            admin_email=settings.notification_admin_email.strip(),
            app_url=settings.public_url.strip(),
        )
    if channel == "smtp":
        if not settings.smtp_host:
            logger.warning("NOTIFICATION_CHANNEL=smtp needs SMTP_HOST; nothing is sent")
            return None
        from app.core.azure import get_credential
        from app.core.notify_email import SmtpNotifier

        return SmtpNotifier(
            get_credential(),
            settings.smtp_host.strip(),
            settings.smtp_port,
            settings.notification_sender.strip() or "rbaclens@localhost",
            admin_email=settings.notification_admin_email.strip(),
            app_url=settings.public_url.strip(),
        )
    logger.warning("notification channel %r is not supported; nothing is sent", channel)
    return None


def enqueue(
    session: Session,
    kind: str,
    *,
    recipient_id: str | None,
    recipient_name: str | None,
    subject_ref: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> Notification | None:
    """Add a notification unless one with the same ``dedupe_key`` exists. The caller commits."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    if (
        session.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key))
        is not None
    ):
        return None
    pending = next(
        (n for n in session.new if isinstance(n, Notification) and n.dedupe_key == dedupe_key), None
    )
    if pending is not None:
        return None
    row = Notification(
        kind=kind,
        recipient_id=recipient_id,
        recipient_name=recipient_name,
        subject_ref=subject_ref,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    session.add(row)
    return row


def deliver_pending(
    session: Session, sender: Notifier, limit: int = 100, now: datetime | None = None
) -> int:
    """Send waiting notifications, oldest first. Commits. Returns how many were sent.

    Too old: closed unsent. Undeliverable: closed with the reason. Any
    other failure: recorded on the row, and delivery pauses for a while.
    """
    global _paused_until
    now = now or datetime.now(UTC)
    if _paused_until and now < _paused_until:
        return 0
    oldest = now - timedelta(days=settings.notification_max_age_days)
    sent = 0
    for n in session.scalars(
        select(Notification)
        .where(Notification.sent_at.is_(None))
        .order_by(Notification.id)
        .limit(limit)
    ).all():
        created = as_utc(n.created_at)
        if created < oldest:
            n.sent_at, n.channel = now, sender.channel
            n.error = f"not sent: older than {settings.notification_max_age_days} days when sending started"
            continue
        try:
            sender.send(n)
        except Undeliverable as exc:
            n.sent_at, n.channel, n.error = now, sender.channel, f"not sent: {exc}"[:500]
            continue
        except Exception as exc:  # noqa: BLE001 - recorded, retried after a pause
            n.error = f"{type(exc).__name__}: {exc}"[:500]
            _paused_until = now + PAUSE_AFTER_FAILURE
            logger.warning("notification %s not sent, pausing delivery: %s", n.id, n.error)
            break
        n.sent_at = now
        n.channel = sender.channel
        n.error = None
        sent += 1
    session.commit()
    return sent


def deliver_now() -> int:
    """One delivery pass with the configured channel (scheduler tick). 0 when none is connected."""
    sender = notifier()
    if sender is None:
        return 0
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        return deliver_pending(session, sender)
