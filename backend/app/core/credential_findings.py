"""Findings on the secrets and certificates of apps holding powerful grants.

An app's secret is a standing key: whoever has it can act as the app. On the
grants of an app that can change who has access or get keys and passwords
(as for owners, ``core/owner_findings.py``):

- ``app_secret_long_lived`` (medium): a secret or certificate valid more than
  two years. The portal allows two at most; longer ones were made by script.
- ``app_secret_expired`` (low): an expired secret or certificate still on the
  app: nothing uses it, remove it.

From the newest check that read the app (``core/owners.current_credentials``):
an app no check read raises nothing. Microsoft's apps are left out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.capabilities import classify
from app.core.owners import CredentialRef, current_credentials, current_owners
from app.db.models import RoleAssignment, RoleDefinition, Scan
from app.tools.graph_resolver import is_microsoft_app

MEDIUM, LOW = "medium", "low"
CATEGORIES = ("app_secret_long_lived", "app_secret_expired")
LONG_LIVED = timedelta(days=731)  # two years, a leap day included


def _evidence(creds: list[CredentialRef]) -> list[dict[str, Any]]:
    return [
        {
            "via": c.via,
            "kind": c.kind,
            "key_id": c.key_id,
            "start_at": c.start_at.isoformat() if c.start_at else None,
            "end_at": c.end_at.isoformat() if c.end_at else None,
        }
        for c in creds
    ]


def _plural(creds: list[CredentialRef]) -> str:
    kinds = {c.kind for c in creds}
    word = kinds.pop() if len(kinds) == 1 else "secret or certificate"
    return f"1 {word}" if len(creds) == 1 else f"{len(creds)} {word}s"


def credential_findings(session: Session, scan: Scan, now: datetime | None = None) -> list:
    """Finding drafts for the powerful grants of ``scan`` held by apps with such secrets."""
    from app.core.findings import FindingDraft

    now = now or datetime.now(UTC)
    ras = [
        ra
        for ra in session.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.scan_id == scan.id)
            .where(RoleAssignment.principal_type == "ServicePrincipal")
        ).all()
        if not is_microsoft_app(getattr(ra, "principal_app_owner", None))
    ]
    if not ras:
        return []
    guids = {ra.role_definition_id.rsplit("/", 1)[-1].lower() for ra in ras}
    levels = {
        rid.rsplit("/", 1)[-1].lower(): classify(perms).level
        for rid, perms in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions)
        ).all()
        if rid.rsplit("/", 1)[-1].lower() in guids
    }
    ras = [
        ra
        for ra in ras
        if levels.get(ra.role_definition_id.rsplit("/", 1)[-1].lower()) in ("access", "keys")
    ]
    apps = {ra.principal_id.lower() for ra in ras}
    creds = current_credentials(session, apps)
    app_ids = {o: info.app_id for o, info in current_owners(session, apps).items()}

    drafts: list[FindingDraft] = []
    for ra in ras:
        mine = creds.get(ra.principal_id.lower()) or []
        if not mine:
            continue
        level = levels[ra.role_definition_id.rsplit("/", 1)[-1].lower()]
        role = ra.role_name or ra.role_definition_id.rsplit("/", 1)[-1]
        place = ra.scope_name or ra.scope.rstrip("/").rsplit("/", 1)[-1] or ra.scope
        what = "change who has access" if level == "access" else "get keys and passwords"
        expired = [c for c in mine if c.end_at is not None and c.end_at < now]
        long_lived = [
            c
            for c in mine
            if c not in expired
            and (c.end_at is None or (c.start_at and c.end_at - c.start_at > LONG_LIVED))
        ]
        for category, severity, listed, detail, suggestion in (
            (
                "app_secret_long_lived",
                MEDIUM,
                long_lived,
                f"This app has {_plural(long_lived)} valid more than two years, and its "
                f"{role} on {place} can {what}: whoever has one can act as the app until then.",
                "Replace it with one valid a year at most, or with sign-in without a secret "
                "(workload identity federation); then delete the old one.",
            ),
            (
                "app_secret_expired",
                LOW,
                expired,
                f"This app still has {_plural(expired)} that expired: nothing can sign in with "
                "it, and it clutters the review of the ones in use.",
                "Delete the expired secret or certificate.",
            ),
        ):
            if not listed:
                continue
            drafts.append(
                FindingDraft(
                    role_assignment_id=ra.id,
                    scope=ra.scope,
                    role_definition_id=ra.role_definition_id,
                    principal_id=ra.principal_id,
                    category=category,
                    severity=severity,
                    title=f"{role} → {ra.principal_name or ra.principal_id} on {place}",
                    detail=detail,
                    suggestion=suggestion,
                    evidence={
                        "scope_level": ra.scope_level,
                        "level": level,
                        "role_type": ra.role_type,
                        "app_id": app_ids.get(ra.principal_id.lower()),
                        "credentials": _evidence(listed),
                    },
                )
            )
    return drafts
