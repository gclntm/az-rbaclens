"""When people last signed in, from the newest check that read it (plan "sign-ins").

A person no check could read (not asked, or Graph refused) is missing: unknown,
not "never". The last sign-in is the newest of the interactive, the
non-interactive and the successful one Graph records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import PrincipalSignIn, Scan

_CHUNK = 500


@dataclass(frozen=True)
class SignInInfo:
    last_at: datetime | None  # newest of the three; None: no sign-in recorded
    account_created_at: datetime | None
    read_at: datetime | None  # when the check read it

    def idle(self, days: int, now: datetime) -> bool:
        """No sign-in for ``days``, and the account is older than that (a new one
        hasn't had the chance)."""
        limit = now - timedelta(days=days)
        if self.last_at is not None:
            return self.last_at < limit
        return self.account_created_at is not None and self.account_created_at < limit


def current_sign_ins(session: Session, principal_ids: set[str]) -> dict[str, SignInInfo]:
    """``{person: SignInInfo}`` for those a completed check read."""
    ids = sorted({p.lower() for p in principal_ids})
    out: dict[str, SignInInfo] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        latest = (
            select(
                PrincipalSignIn.principal_id,
                func.max(PrincipalSignIn.scan_id).label("scan_id"),
            )
            .join(Scan, Scan.id == PrincipalSignIn.scan_id)
            .where(Scan.status == "done", PrincipalSignIn.principal_id.in_(chunk))
            .group_by(PrincipalSignIn.principal_id)
            .subquery()
        )
        for row, finished in session.execute(
            select(PrincipalSignIn, Scan.finished_at)
            .join(
                latest,
                (latest.c.principal_id == PrincipalSignIn.principal_id)
                & (latest.c.scan_id == PrincipalSignIn.scan_id),
            )
            .join(Scan, Scan.id == PrincipalSignIn.scan_id)
        ).all():
            dates = [
                as_utc(d)
                for d in (row.last_sign_in_at, row.last_non_interactive_at, row.last_successful_at)
                if d is not None
            ]
            out[row.principal_id] = SignInInfo(
                last_at=max(dates) if dates else None,
                account_created_at=as_utc(row.account_created_at),
                read_at=as_utc(finished),
            )
    return out
