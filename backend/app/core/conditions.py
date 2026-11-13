"""Role-assignment conditions (Azure ABAC) in plain words.

A condition narrows one role assignment: "may assign roles, except Owner";
"may read blobs, only in container X". The language (ABAC v2.0) is a full
expression syntax; this module does not parse it. It recognises the
templates the Azure portal writes — which is what conditions in practice
are — and says what they mean:

- **delegation**: limits on ``roleAssignments/write`` / ``delete`` — which
  roles may be assigned (``GuidEquals`` = only these, ``GuidNotEquals`` =
  all except these) and to which principal types or principals;
- **storage**: limits on blob / container access by container name, blob
  path or blob index tag.

Anything else is ``other`` — shown as "limited by a condition, check it",
never treated as unlimited and never as harmless. Spellings vary in the
wild (``&&`` / ``AND``, ``||`` / ``OR``, GUIDs with or without dashes,
spaces around braces); matching is tolerant of all of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ConditionKind = Literal["delegation", "storage", "other"]

# Built-in roles that can hand out access; a delegation that excludes all
# three cannot create another administrator.
ADMIN_ROLES = {
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635": "Owner",
    "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9": "User Access Administrator",
    "f58310d9-a9f6-439a-9e8d-f62e7b41a168": "Role Based Access Control Administrator",
}

_ROLE_DEF = re.compile(
    r"@(?:Request|Resource)\[Microsoft\.Authorization/roleAssignments:RoleDefinitionId\]\s*"
    r"ForAny(?:Of)?(?:All|Any)Values:Guid(Not)?Equals\s*\{([^}]*)\}",
    re.IGNORECASE,
)
_PRINCIPAL_TYPE = re.compile(
    r"@Request\[Microsoft\.Authorization/roleAssignments:PrincipalType\]\s*"
    r"ForAny(?:Of)?(?:All|Any)Values:StringEqualsIgnoreCase\s*\{([^}]*)\}",
    re.IGNORECASE,
)
_PRINCIPAL_ID = re.compile(
    r"@Request\[Microsoft\.Authorization/roleAssignments:PrincipalId\]\s*"
    r"ForAny(?:Of)?(?:All|Any)Values:GuidEquals\s*\{([^}]*)\}",
    re.IGNORECASE,
)
_CONTAINER = re.compile(
    r"@Resource\[Microsoft\.Storage/storageAccounts/blobServices/containers:name\]\s*"
    r"StringEquals(?:IgnoreCase)?\s*'([^']*)'",
    re.IGNORECASE,
)
_BLOB_PATH = re.compile(
    r"@Resource\[Microsoft\.Storage/storageAccounts/blobServices/containers/blobs:path\]\s*"
    r"StringLike(?:IgnoreCase)?\s*'([^']*)'",
    re.IGNORECASE,
)
_BLOB_TAG = re.compile(
    r"@Resource\[Microsoft\.Storage/storageAccounts/blobServices/containers/blobs/tags:([^<\]]+)"
    r"(?:<\$key_case_sensitive\$>)?\]\s*StringEquals(?:IgnoreCase)?\s*'([^']*)'",
    re.IGNORECASE,
)
_GUID_HEX = re.compile(r"[0-9a-f]{32}")


def _guid(value: str) -> str | None:
    hexes = re.sub(r"[^0-9a-f]", "", value.lower())
    if not _GUID_HEX.fullmatch(hexes):
        return None
    return f"{hexes[:8]}-{hexes[8:12]}-{hexes[12:16]}-{hexes[16:20]}-{hexes[20:]}"


def _guids(group: str) -> list[str]:
    return [g for g in (_guid(v) for v in group.split(",")) if g]


def _strings(group: str) -> list[str]:
    return [v.strip().strip("'\"") for v in group.split(",") if v.strip()]


@dataclass
class ConditionSummary:
    kind: ConditionKind
    # Plain-language sentence for the UI.
    text: str
    # Delegation: role GUIDs that may / may not be assigned.
    only_roles: list[str] = field(default_factory=list)
    except_roles: list[str] = field(default_factory=list)
    principal_types: list[str] = field(default_factory=list)
    # True when the delegation cannot create Owner / UAA / RBAC Administrator.
    blocks_admin_roles: bool = False


def _names(guids: list[str], names: dict[str, str]) -> str:
    labels = [names.get(g) or ADMIN_ROLES.get(g) or f"role {g[:8]}…" for g in guids]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def summarise(
    condition: str | None, role_names: dict[str, str] | None = None
) -> ConditionSummary | None:
    """Plain-language summary of one assignment's ``condition``, or None."""
    if not condition or not condition.strip():
        return None
    names = {k.lower(): v for k, v in (role_names or {}).items()}

    only: list[str] = []
    excluded: list[str] = []
    for negated, group in _ROLE_DEF.findall(condition):
        target = excluded if negated else only
        target.extend(g for g in _guids(group) if g not in target)
    types = [t for m in _PRINCIPAL_TYPE.findall(condition) for t in _strings(m)]
    principals = [g for m in _PRINCIPAL_ID.findall(condition) for g in _guids(m)]

    if only or excluded or types or principals:
        parts = []
        if only:
            parts.append(f"can only grant {_names(only, names)}")
        elif excluded:
            parts.append(f"can grant any role except {_names(excluded, names)}")
        else:
            parts.append("can grant roles")
        if types:
            readable = {
                "serviceprincipal": "service principals",
                "user": "users",
                "group": "groups",
            }
            parts.append(
                "only to " + " and ".join(readable.get(t.lower(), t) for t in dict.fromkeys(types))
            )
        if principals:
            parts.append(
                f"only to {len(principals)} specific identit{'y' if len(principals) == 1 else 'ies'}"
            )
        text = ", ".join(parts)
        return ConditionSummary(
            kind="delegation",
            text=text[0].upper() + text[1:],
            only_roles=only,
            except_roles=excluded,
            principal_types=list(dict.fromkeys(types)),
            blocks_admin_roles=bool(
                (excluded and set(ADMIN_ROLES) <= set(excluded))
                or (only and not set(only) & set(ADMIN_ROLES))
            ),
        )

    storage = (
        [f"container '{c}'" for c in _CONTAINER.findall(condition)]
        + [f"blobs under '{p}'" for p in _BLOB_PATH.findall(condition)]
        + [f"blobs tagged {k}={v}" for k, v in _BLOB_TAG.findall(condition)]
    )
    if storage:
        return ConditionSummary(kind="storage", text="Only " + ", ".join(dict.fromkeys(storage)))

    return ConditionSummary(kind="other", text="Limited by a condition, check it")
