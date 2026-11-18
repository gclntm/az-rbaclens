"""What an Entra role lets someone do, on the same five levels as Azure roles.

Entra ID is a place above the Tenant Root Group (``ENTRA_PLACE``): some of
its roles lead straight into Azure access. A role's permissions are
Microsoft Graph's ``rolePermissions`` (``allowedResourceActions``, such as
``microsoft.directory/groups/members/update``), cached in
``role_definitions.permissions`` as Graph returns them; ``capabilities.classify``
sends them here.

1. ``access``: can change who has access. Role assignments and PIM
   (Privileged Role Administrator, Global Administrator), the members of
   security groups or their dynamic rules (Groups / User Administrator),
   access packages.
2. ``keys``: can get in as someone or something else. Add a secret to an
   app (Application Administrator), reset a password or sign-in methods
   (Helpdesk, Authentication Administrator), read BitLocker or local admin
   passwords. Like Azure keys, these keep working after the role is removed.
3. ``change``: changes Entra or Microsoft 365 settings, not who has access.
   Changing only the members of Microsoft 365 groups (Exchange, SharePoint,
   Teams administrators) lands here, named on the row: such a group rarely
   holds an Azure role.
4. ``view``: reads only.

A permission with a ``condition`` reaches only the objects someone owns
(``$SubjectIsOwner``) or themselves (``$ResourceIsSelf``): not counted
here. What owners can do is a finding of its own.

Every role carries ``microsoft.azure.serviceHealth`` / ``supportTickets``
(open a support case): not counted, as ``Microsoft.Support/*`` isn't in Azure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Entra ID as a place: Entra's resource provider at tenant scope, a real ARM path.
ENTRA_PLACE = "/providers/microsoft.aadiam"
ENTRA_PLACE_NAME = "Entra ID"
ENTRA_LEVEL = "directory"
ENTRA_BUILTIN = "EntraBuiltInRole"
ENTRA_CUSTOM = "EntraCustomRole"
ENTRA_ROLE_TYPES = (ENTRA_BUILTIN, ENTRA_CUSTOM)
GLOBAL_ADMINISTRATOR = "62e90394-69f5-4237-9190-012177145e10"

_IGNORED = re.compile(
    r"^microsoft\.(azure|office365)\.(servicehealth|supporttickets|webportal|messagecenter)/"
)
_READ = re.compile(r"/(read|restrictedread)$")

# (pattern, what it gives, in words). The words show on the row, like Azure's keys.
_ACCESS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), words)
    for p, words in (
        (
            r"^microsoft\.directory/(roleassignments|scopedrolememberships|"
            r"roleeligibilityschedules|roleassignmentschedules)/",
            "Entra roles",
        ),
        (
            r"^microsoft\.directory/groups(\.security)?/(members|dynamicmembershiprule)/",
            "group members",
        ),
        (
            r"^microsoft\.directory/groups(\.security)?/allproperties/(alltasks|update)$",
            "group members",
        ),
        (r"^microsoft\.directory/entitlementmanagement/allproperties/alltasks$", "access packages"),
    )
)
_KEYS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), words)
    for p, words in (
        (
            r"^microsoft\.directory/(applications|serviceprincipals|agentidentityblueprints)"
            r"(\.[a-z]+)?/(credentials/update|allproperties/(alltasks|update))$",
            "app secrets",
        ),
        (
            r"^microsoft\.directory/serviceprincipals/"
            r"(getpasswordsinglesignoncredentials|managepasswordsinglesignoncredentials|"
            r"synchronizationcredentials/manage)$",
            "app sign-in passwords",
        ),
        (r"^microsoft\.directory/users/(password/update|allproperties/alltasks)$", "passwords"),
        (r"^microsoft\.directory/users/authenticationmethods/(?!.*read$)", "sign-in methods"),
        (r"^microsoft\.directory/bitlockerkeys/key/read$", "BitLocker keys"),
        (r"^microsoft\.directory/devicelocalcredentials/password/read$", "local admin passwords"),
    )
)


# Members of Microsoft 365 groups only: a change, named on the row.
_M365_MEMBERS = re.compile(
    r"^microsoft\.directory/groups\.unified/(members|dynamicmembershiprule|allproperties)/"
)


def is_entra(permissions: Any) -> bool:
    """Graph's shape: blocks with ``allowedResourceActions`` (Azure's have ``actions``)."""
    return any(isinstance(b, dict) and "allowedResourceActions" in b for b in permissions or [])


def actions(permissions: Any) -> list[str]:
    """Lowercased allowed actions, leaving out blocks with a ``condition``
    (own objects only)."""
    return [
        a.lower()
        for b in permissions or []
        if isinstance(b, dict) and not b.get("condition")
        for a in b.get("allowedResourceActions") or []
    ]


def _matches(rules: Iterable[tuple[re.Pattern[str], str]], acts: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(words for p, words in rules for a in acts if p.search(a)))


def level_of(permissions: Any) -> tuple[str, tuple[str, ...]]:
    """``(level, what it gives)`` for an Entra role's permissions."""
    if not is_entra(permissions):
        return "unknown", ()
    acts = [a for a in actions(permissions) if not _IGNORED.search(a)]
    writes = [a for a in acts if not _READ.search(a)]
    keys = _matches(_KEYS, acts)  # a few keys are reads: BitLocker, local admin passwords
    if gives := _matches(_ACCESS, writes):
        return "access", gives + keys
    if keys:
        return "keys", keys
    if writes:
        m365 = (
            ("Microsoft 365 group members",) if any(_M365_MEMBERS.search(a) for a in writes) else ()
        )
        return "change", m365
    return "view", ()


def is_high_risk(permissions: Any) -> bool:
    """High-risk like an Azure role that can change access: access or keys."""
    return level_of(permissions)[0] in ("access", "keys")
