"""Findings on Entra roles (plan item 1, step 4).

Usage findings don't apply to Entra roles: the Activity Log doesn't record
Entra actions (``core/scan_coverage.py`` leaves them out). These come from
what a check read instead:

- ``entra_standing_admin``: a person holds, all the time, an Entra role that
  can change who has access. Break-glass identities (Settings → Advice) are
  left out; they are meant to be standing.
- ``entra_too_many_global_admins``: more than four identities hold Global
  Administrator, standing or eligible. Microsoft advises fewer than five.
- ``entra_app_privileged``: an app holds an Entra role that can change who has
  access or get in as someone else; its secret is a standing key.
- ``entra_role_via_group``: such a role comes through a group, so whoever
  manages the group's members decides who holds it.
- ``entra_admin_idle``: a person holds, standing, an Entra role that can change
  who has access or get keys, and hasn't signed in for the idle threshold
  (Settings › Advice), or never while the account is older than that. From the
  sign-in dates a check read (``core/signins.py``); unknown dates raise nothing.
  Break-glass identities are left out: they are meant to sit idle.
- ``foreign_group`` (shared with Azure places, ``core/findings.py``): such a
  role is held by a group of another tenant (a partner, through delegated
  admin access): its members are managed there, and can't be seen from here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entra_roles import ENTRA_PLACE, GLOBAL_ADMINISTRATOR, level_of
from app.core.signins import current_sign_ins
from app.core.timeutil import as_utc
from app.db.models import RoleAssignment, RoleDefinition, RoleEligibility, Scan

HIGH, MEDIUM = "high", "medium"
CATEGORIES = (
    "foreign_group",
    "entra_standing_admin",
    "entra_app_privileged",
    "entra_too_many_global_admins",
    "entra_role_via_group",
    "entra_admin_idle",
)
GLOBAL_ADMINS_ADVISED_MAX = 4

_WHAT = {
    "access": "can change who has access in Entra ID, and through it in Azure",
    "keys": "can get in as someone or something else (passwords, sign-in methods or app secrets)",
}


def entra_findings(session: Session, scan: Scan, break_glass: set[str]) -> list:
    """Finding drafts for the Entra grants of ``scan`` (empty when it has none)."""
    from app.core.findings import FOREIGN_GROUP_DETAIL, FOREIGN_GROUP_SUGGESTION, FindingDraft

    ras = session.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.scan_id == scan.id)
        .where(RoleAssignment.scope == ENTRA_PLACE)
    ).all()
    if not ras:
        return []
    guids = {ra.role_definition_id.rsplit("/", 1)[-1].lower() for ra in ras}
    levels = {
        rid.lower(): level_of(perms)[0]
        for rid, perms in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions).where(
                RoleDefinition.role_definition_id.in_(guids)
            )
        ).all()
    }
    ga_holders = {
        ra.principal_id.lower()
        for ra in ras
        if ra.role_definition_id.lower().endswith(GLOBAL_ADMINISTRATOR)
    } | {
        pid.lower()
        for (pid,) in session.execute(
            select(RoleEligibility.principal_id)
            .where(RoleEligibility.scan_id == scan.id)
            .where(RoleEligibility.scope == ENTRA_PLACE)
            .where(RoleEligibility.role_definition_id.ilike(f"%{GLOBAL_ADMINISTRATOR}"))
        ).all()
    }

    from app.core.advice import load_settings

    idle_days = load_settings(session).idle_days
    now = datetime.now(UTC)
    sign_ins = current_sign_ins(
        session, {ra.principal_id for ra in ras if ra.principal_type == "User"}
    )

    drafts: list[FindingDraft] = []
    for ra in ras:
        level = levels.get(ra.role_definition_id.rsplit("/", 1)[-1].lower(), "unknown")
        role = ra.role_name or "An Entra role"

        def add(
            category: str,
            severity: str,
            detail: str,
            suggestion: str,
            ra: RoleAssignment = ra,
            level: str = level,
            **extra: object,
        ) -> None:
            drafts.append(
                FindingDraft(
                    role_assignment_id=ra.id,
                    scope=ra.scope,
                    role_definition_id=ra.role_definition_id,
                    principal_id=ra.principal_id,
                    category=category,
                    severity=severity,
                    title=f"{ra.role_name or 'An Entra role'} → "
                    f"{ra.principal_name or ra.principal_id} on Entra ID",
                    detail=detail,
                    suggestion=suggestion,
                    evidence={
                        "scope_level": "directory",
                        "level": level,
                        "role_type": ra.role_type,
                        **extra,
                    },
                )
            )

        powerful = level in ("access", "keys")
        standing = (ra.assignment_type or "Assigned") != "Activated"
        if (
            level == "access"
            and ra.principal_type == "User"
            and standing
            and ra.principal_id.lower() not in break_glass
        ):
            add(
                "entra_standing_admin",
                HIGH,
                f"{role} {_WHAT['access']}, all the time.",
                "Make it eligible through PIM, so it is switched on only when needed, with a reason.",
            )
        info = sign_ins.get(ra.principal_id.lower())
        recent_grant = ra.created_on is not None and as_utc(ra.created_on) > now - timedelta(
            days=idle_days
        )
        if (
            powerful
            and ra.principal_type == "User"
            and standing
            and ra.principal_id.lower() not in break_glass
            and info is not None
            and info.idle(idle_days, now)
            and not recent_grant
        ):
            when = (
                f"they haven't signed in for {idle_days} days (last on {info.last_at:%d %b %Y})"
                if info.last_at
                else "they have never signed in"
            )
            add(
                "entra_admin_idle",
                HIGH,
                f"{role} {_WHAT[level]}, and {when}.",
                "Remove the role; if the account is no longer used, disable it.",
                last_sign_in_at=info.last_at.isoformat() if info.last_at else None,
                idle_days=idle_days,
            )
        if powerful and ra.principal_type == "ServicePrincipal":
            add(
                "entra_app_privileged",
                HIGH,
                f"An app holds {role}: it {_WHAT[level]}. Whoever has its secret or certificate can too.",
                "Check the app needs it; give it a narrower role or Graph permission if not.",
            )
        if powerful and ra.principal_type == "ForeignGroup":
            add(
                "foreign_group",
                HIGH,
                f"{FOREIGN_GROUP_DETAIL.format(role=role)} The role {_WHAT[level]}.",
                FOREIGN_GROUP_SUGGESTION,
            )
        if powerful and ra.principal_type == "Group":
            add(
                "entra_role_via_group",
                MEDIUM,
                f"{role} comes through this group: whoever manages its members decides who {_WHAT[level]}.",
                "Check who owns and manages the group, and that it is role-assignable.",
            )
        if (
            ra.role_definition_id.lower().endswith(GLOBAL_ADMINISTRATOR)
            and len(ga_holders) > GLOBAL_ADMINS_ADVISED_MAX
        ):
            add(
                "entra_too_many_global_admins",
                MEDIUM,
                f"{len(ga_holders)} identities hold Global Administrator, standing or through PIM; "
                f"Microsoft advises fewer than {GLOBAL_ADMINS_ADVISED_MAX + 1}.",
                "Give narrower roles to those who don't need everything.",
                holders=len(ga_holders),
            )
    return drafts
