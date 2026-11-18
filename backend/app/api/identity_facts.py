"""Facts about an identity shared by its page and its grants' pages: when a person
last signed in, and an app's secrets and certificates."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.credential_findings import LONG_LIVED
from app.core.owners import current_credentials
from app.core.signins import current_sign_ins
from app.core.visibility import Visibility
from app.schemas.principals import CredentialRead, SignInRead


def sees_sign_ins(vis: Visibility | None, pid: str) -> bool:
    """Sign-in dates are personal: admins (or sign-in off) and the person only,
    like what they did in the Activity Log."""
    return vis is None or pid.lower() in vis.my_ids


def sign_in_read(
    session: Session, pid: str, principal_type: str | None, vis: Visibility | None
) -> SignInRead | None:
    if principal_type != "User" or not sees_sign_ins(vis, pid):
        return None
    info = current_sign_ins(session, {pid}).get(pid.lower())
    if info is None:
        return None
    from app.core.advice import load_settings

    days = load_settings(session).idle_days
    return SignInRead(
        last_at=info.last_at,
        read_at=info.read_at,
        idle=info.idle(days, datetime.now(UTC)),
        idle_days=days,
    )


def credentials_read(
    session: Session, pid: str, principal_type: str | None
) -> list[CredentialRead] | None:
    if principal_type != "ServicePrincipal":
        return None
    found = current_credentials(session, {pid})
    if pid.lower() not in found:
        return None
    now = datetime.now(UTC)
    return [
        CredentialRead(
            via=c.via,
            kind=c.kind,
            start_at=c.start_at,
            end_at=c.end_at,
            expired=c.end_at is not None and c.end_at < now,
            long_lived=c.end_at is None
            or (c.start_at is not None and c.end_at - c.start_at > LONG_LIVED),
        )
        for c in found[pid.lower()]
    ]
