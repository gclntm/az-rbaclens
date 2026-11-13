"""Email channels for the notification outbox.

- ``email``: Microsoft Graph. Sends as ``NOTIFICATION_SENDER`` with
  ``POST /users/{sender}/sendMail``, which needs the ``Mail.Send``
  application permission (restrict it to that one mailbox with an Exchange
  application access policy).
- ``smtp``: plain SMTP to ``SMTP_HOST:SMTP_PORT``, e.g. the Mailpit
  container of the test and prod stacks, which keeps every message in its
  web inbox instead of delivering it.

Recipients are Entra ID object ids from the outbox, turned into addresses
with Graph; messages for the admins' review go to ``NOTIFICATION_ADMIN_EMAIL``.
"""

from __future__ import annotations

import html
import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx
from azure.core.credentials import TokenCredential

from app.core.notify import Undeliverable
from app.db.models import Notification
from app.tools.graph_resolver import GRAPH_SCOPE, _build_ssl_context, find_user, looks_like_email

logger = logging.getLogger(__name__)

SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

AddressOf = Callable[[str], str | None]


def _p(n: Notification, key: str) -> str:
    value = (n.payload or {}).get(key)
    return "" if value is None else str(value)


def compose(n: Notification, app_url: str) -> tuple[str, str]:
    """``(subject, HTML body)`` for a notification. Every value is escaped."""

    def e(key: str) -> str:
        return html.escape(_p(n, key))

    place = e("place") or "everything nobody is responsible for"
    link = ""
    if app_url and _p(n, "path"):
        url = html.escape(app_url.rstrip("/") + _p(n, "path"), quote=True)
        link = f'<p><a href="{url}">Open it in RBAC Lens</a></p>'
    note = f"<p>Note from the reviewer: “{e('note')}”</p>" if _p(n, "note") else ""
    if n.kind == "ask":
        if (n.payload or {}).get("to_the_person"):
            subject = f"Do you still need {_p(n, 'role') or 'this access'} on {_p(n, 'place')}?"
            body = (
                f"<p>{e('reviewer') or 'A reviewer'} is reviewing who can do what on {e('place')} "
                f"and asks whether you still need <strong>{e('role')}</strong> there.</p>{note}"
                "<p>Please reply to the reviewer. RBAC Lens never changes Azure on its own.</p>"
            )
        else:
            subject = f"Question about {_p(n, 'who')} on {_p(n, 'place')}"
            body = (
                f"<p>In the {e('period')} review, a question was marked about <strong>{e('who')}</strong> "
                f"({e('role')} on {e('place')}).</p>{note}"
            )
    elif n.kind == "review_opened":
        subject = f"Your review of {_p(n, 'place') or 'the unassigned places'} for {_p(n, 'period')} is open"
        body = (
            f"<p>The monthly access review of <strong>{place}</strong> for {e('period')} is open. "
            "For each grant, decide: keep it, remove it, or ask.</p>"
        )
    elif n.kind == "todo_overdue":
        subject = f"{_p(n, 'count')} change(s) from your reviews still to make in Azure"
        body = (
            f"<p><strong>{e('count')}</strong> change(s) decided in your access reviews are still open after 14 days: "
            "a grant to remove, or to change. RBAC Lens never changes Azure; the list has a ready-to-run "
            "command for each, and the next daily check ticks it off.</p>"
        )
    elif n.kind == "data_stale":
        subject = f"RBAC Lens data is not up to date: {_p(n, 'count')} problem(s)"
        lines = "".join(
            f"<li>{html.escape(str(line))}</li>" for line in (n.payload or {}).get("lines") or []
        )
        body = (
            f"<p>The access picture in RBAC Lens has stopped being kept up to date:</p><ul>{lines}</ul>"
            "<p>Data &amp; schedules shows each schedule, its last run and its last error.</p>"
        )
    elif n.kind == "review_due":
        subject = f"{_p(n, 'left')} still to decide in your {_p(n, 'period')} review"
        body = f"<p>Your access review for {e('period')} is due soon, with <strong>{e('left')}</strong> still to decide.</p>"
    else:
        subject = f"Still open: the question about {_p(n, 'who')}"
        body = (
            f"<p>The question about <strong>{e('who')}</strong> ({e('role')} on {e('place')}) "
            f"is still open in the {e('period')} review.</p>{note}"
        )
    return (
        subject,
        f'<div style="font-family:sans-serif;font-size:14px">{body}{link}<p style="color:#64748b">RBAC Lens</p></div>',
    )


class _Addresses:
    """Entra object id → email address, cached for the notifier's lifetime."""

    credential: TokenCredential
    admin_email: str
    _address_of: AddressOf

    def _graph_address(self, object_id: str) -> str | None:
        # strict: Graph unreachable is a failure to retry, not "no address".
        user = find_user(object_id, self.credential, strict=True)
        return user.email if user else None

    def address(self, recipient_id: str | None) -> str | None:
        if not recipient_id:
            return self.admin_email or None
        if recipient_id not in self._cache:
            self._cache[recipient_id] = self._address_of(recipient_id)
        return self._cache[recipient_id]

    def send_to(
        self, to: str, subject: str, html_body: str
    ) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def send(self, notification: Notification) -> None:
        to = self.address(notification.recipient_id)
        if not to:
            raise Undeliverable(
                "no email address for this person"
                if notification.recipient_id
                else "no admin address set (NOTIFICATION_ADMIN_EMAIL)"
            )
        subject, body = compose(notification, self.app_url)
        self.send_to(to, subject, body)


class SmtpNotifier(_Addresses):
    """Plain SMTP (no authentication), meant for Mailpit inside the stack."""

    channel = "smtp"

    def __init__(
        self,
        credential: TokenCredential,
        host: str,
        port: int,
        sender: str,
        admin_email: str = "",
        app_url: str = "",
        address_of: AddressOf | None = None,
        smtp_factory: Callable[..., Any] = smtplib.SMTP,
    ) -> None:
        self.credential = credential
        self.host, self.port, self.sender = host, port, sender
        self.admin_email = admin_email
        self.app_url = app_url
        self._cache: dict[str, str | None] = {}
        self._address_of = address_of or self._graph_address
        self._smtp = smtp_factory

    def send_to(self, to: str, subject: str, html_body: str) -> None:
        if not looks_like_email(to):
            raise Undeliverable(f"not an email address: {to!r}")
        msg = EmailMessage()
        msg["Subject"] = subject[:250]
        msg["From"] = self.sender
        msg["To"] = to
        msg.set_content("This message is best read in an email app that shows HTML.")
        msg.add_alternative(html_body, subtype="html")
        with self._smtp(self.host, self.port, timeout=15) as client:
            client.send_message(msg)


class EmailNotifier(_Addresses):
    channel = "email"

    def __init__(
        self,
        credential: TokenCredential,
        sender: str,
        admin_email: str = "",
        app_url: str = "",
        address_of: AddressOf | None = None,
        post: Callable[[str, dict[str, Any], str], httpx.Response] | None = None,
    ) -> None:
        self.credential = credential
        self.sender = sender
        self.admin_email = admin_email
        self.app_url = app_url
        self._cache: dict[str, str | None] = {}
        self._address_of = address_of or self._graph_address
        self._post = post or self._graph_post

    def _graph_post(self, url: str, body: dict[str, Any], token: str) -> httpx.Response:
        with httpx.Client(timeout=20.0, verify=_build_ssl_context()) as client:
            return client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})

    def send_to(self, to: str, subject: str, html_body: str) -> None:
        if not looks_like_email(to):
            raise Undeliverable(f"not an email address: {to!r}")
        token = self.credential.get_token(GRAPH_SCOPE).token
        body = {
            "message": {
                "subject": subject[:250],
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": False,
        }
        response = self._post(SEND_URL.format(sender=self.sender), body, token)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise RuntimeError(f"Graph sendMail answered {response.status_code}: {detail}")
