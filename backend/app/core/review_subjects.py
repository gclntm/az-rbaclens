"""Owners and secrets as review items (plan "owners and secrets in the monthly review").

The monthly review takes exactly what the findings flag: one item per owner
link (an app or group, one owner) listed by an owner finding, and one per
secret or certificate listed by a secret finding, whatever the number of
grants they reach. Each item belongs to the round that reviews the app's or
group's strongest grant (highest level, then the broadest place), so it has
one reviewer, who already sees that grant.

Commands and to-do closing per kind live here too, so ``core/reviews.py``
and ``core/todos.py`` stay about grants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.advice_texts import RULES, _q, credential_delete_commands, owner_remove_commands
from app.core.capabilities import LEVEL_RANK
from app.core.current_view import CurrentView
from app.db.models import RoleAssignment

OWNER_CATEGORIES = ("app_owners", "app_owned_by_app", "group_owners", "guest_owner")
SECRET_CATEGORIES = ("app_secret_long_lived", "app_secret_expired")
KINDS = ("grant", "owner", "secret")
_SCOPE_RANK = {
    "directory": -1,
    "mgmt_group": 0,
    "subscription": 1,
    "resource_group": 2,
    "resource": 3,
}


@dataclass
class Subject:
    kind: str  # owner | secret
    key: str
    principal_id: str
    principal_name: str | None
    principal_type: str | None
    detail: dict[str, Any]
    ras: list[RoleAssignment] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)

    def strongest(self, view: CurrentView) -> RoleAssignment:
        return min(
            self.ras,
            key=lambda ra: (
                LEVEL_RANK[view.capability_of(ra).level],
                _SCOPE_RANK.get(ra.scope_level, 9),
                ra.scope,
            ),
        )

    def suggestion(self) -> tuple[str | None, str | None, str | None]:
        """(decision, rule, title): a guest owner is asked about; an expired secret
        removed, a long-lived one changed; other owners get none (the reviewer
        knows who must stay)."""
        if self.kind == "owner":
            rule = "R18 guest-owner" if "guest_owner" in self.categories else None
            return ("ask", rule, RULES[rule].title) if rule else (None, None, None)
        if "app_secret_expired" in self.categories:
            return "remove", "R20 expired-secret", RULES["R20 expired-secret"].title
        return "change", "R19 long-lived-secret", RULES["R19 long-lived-secret"].title


def owner_key(object_id: str, owner_id: str) -> str:
    return f"owner:{object_id.lower()}:{owner_id.lower()}"


def secret_key(object_id: str, via: str, key_id: str) -> str:
    return f"secret:{object_id.lower()}:{via}:{key_id.lower()}"


def subjects(view: CurrentView) -> list[Subject]:
    """Every owner link and secret the current findings flag (any decision)."""
    out: dict[str, Subject] = {}
    for ra in view.ras:
        for ref in view.findings_by_ra.get(ra.id, ()):
            f = ref.finding
            ev = f.evidence or {}
            obj = ra.principal_id.lower()
            if f.category in OWNER_CATEGORIES:
                for o in ev.get("owners") or []:
                    if not o.get("id"):
                        continue
                    key = owner_key(obj, o["id"])
                    s = out.get(key) or out.setdefault(
                        key,
                        Subject(
                            kind="owner",
                            key=key,
                            principal_id=str(o["id"]).lower(),
                            principal_name=o.get("name"),
                            principal_type=o.get("type"),
                            detail={
                                "object_id": obj,
                                "object_name": ra.principal_name,
                                "object_kind": ev.get("object_kind")
                                or ("app" if ra.principal_type == "ServicePrincipal" else "group"),
                                "via": list(o.get("via") or []),
                                "app_id": ev.get("app_id"),
                                "owner_id": str(o["id"]).lower(),
                                "owner_name": o.get("name"),
                            },
                        ),
                    )
                    s.ras.append(ra)
                    s.categories.add(f.category)
            elif f.category in SECRET_CATEGORIES:
                for c in ev.get("credentials") or []:
                    if not c.get("key_id"):
                        continue
                    key = secret_key(obj, c.get("via") or "", c["key_id"])
                    s = out.get(key) or out.setdefault(
                        key,
                        Subject(
                            kind="secret",
                            key=key,
                            principal_id=obj,
                            principal_name=ra.principal_name,
                            principal_type=ra.principal_type,
                            detail={
                                "object_id": obj,
                                "object_name": ra.principal_name,
                                "app_id": ev.get("app_id"),
                                "via": c.get("via"),
                                "kind": c.get("kind"),
                                "key_id": c["key_id"],
                                "start_at": c.get("start_at"),
                                "end_at": c.get("end_at"),
                            },
                        ),
                    )
                    s.ras.append(ra)
                    s.categories.add(f.category)
    return list(out.values())


# ---- commands ------------------------------------------------------------------------------


def commands(
    kind: str, decision: str, detail: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    """``(how, commands)`` for removing an owner, or deleting / replacing a secret."""
    d = detail or {}
    obj, app_id = d.get("object_id") or "", d.get("app_id")
    name = d.get("object_name") or obj
    if kind == "owner":
        owner = {"id": d.get("owner_id"), "name": d.get("owner_name"), "via": d.get("via") or []}
        where = (
            f"App registrations › {name} › Owners, and Enterprise applications › {name} › Owners"
            if d.get("object_kind") == "app"
            else f"Groups › {name} › Owners"
        )
        return (
            [f"Remove the owner with the commands below, or in the Entra admin center: {where}."],
            [c for c in owner_remove_commands(obj, app_id, [owner]) if not c.startswith("#")],
        )
    cred = {"via": d.get("via"), "kind": d.get("kind"), "key_id": d.get("key_id")}
    delete = credential_delete_commands(obj, app_id, [cred])
    portal = f"Or in the Entra admin center: App registrations › {name} › Certificates & secrets."
    if decision == "remove":
        return [f"Delete the {d.get('kind') or 'secret'} with the command below.", portal], delete
    new = (
        [f"az ad app credential reset --id {_q(app_id)} --append --years 1"]
        if app_id and d.get("via") == "registration"
        else []
    )
    return (
        [
            "Prefer sign-in without a secret (workload identity federation). Otherwise add one "
            "valid a year (the command prints it once), put it where the app uses its secret, "
            "check it works, then delete the old one:",
            portal,
        ],
        [*new, *delete],
    )


def still_there(kind: str, detail: dict[str, Any] | None, owners: dict, creds: dict) -> bool | None:
    """Is the owner link or secret still there? ``None``: unknown (not read)."""
    d = detail or {}
    obj = (d.get("object_id") or "").lower()
    if kind == "owner":
        info = owners.get(obj)
        if info is None or not info.known:
            return None
        return any(o.owner_id == (d.get("owner_id") or "").lower() for o in info.owners)
    if obj not in creds:
        return None
    return any(
        c.key_id == (d.get("key_id") or "").lower() and c.via == d.get("via") for c in creds[obj]
    )
