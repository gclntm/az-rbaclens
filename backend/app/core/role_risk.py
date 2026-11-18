"""Role-power risk classifier.

A role assignment is "risky" along two independent axes:

  1. **Role power** — what the role *can* do (the actions list).
  2. **Blast radius** — *where* the assignment sits (mgmt group >
     subscription > resource group > resource).

This module owns axis (1) only. The frontend combines it with the
RA's ``scope_level`` to decide whether to draw the "R" chip — that
keeps the rule "Owner at MG = scary, Owner at RG = noisy but
expected" intact.

Previously the frontend hardcoded a 3-role allowlist
(``Owner / Contributor / User Access Administrator``). That misses
custom roles that mimic Owner via wildcards, and also misses
built-ins like ``User Access Administrator`` reskins or
``Reservations Administrator``. Driving the classification off the
actual permissions list catches both.

The classifier is intentionally coarse — one bit, ``high`` or
``None`` — because reviewers parse a chip-or-no-chip much faster
than a numeric score, and any finer gradation invites bikeshedding
over thresholds. The role-detail drawer shows the full actions list
when someone wants the nuance.
"""

from __future__ import annotations

from typing import Any

# Action *patterns* that earn a role the "high" classification. Match
# is case-insensitive equality on the action string. Anything ending in
# ``/write``, ``/delete``, or the bare ``*`` confers broad mutating
# power; ``Microsoft.Authorization`` actions specifically grant the
# ability to *re-grant* (privilege escalation).
_HIGH_POWER_EXACT: frozenset[str] = frozenset(
    s.lower()
    for s in (
        "*",
        "*/write",
        "*/delete",
        "*/action",
    )
)


def _is_high_power_action(action: str) -> bool:
    """True iff ``action`` grants broad write / mutation / RBAC power.

    Decisions, in order:

    - ``*`` and the ``*/write|delete|action`` family → high.
    - Anything under ``Microsoft.Authorization/*`` that ends in
      ``/write`` or ``/delete`` (i.e. mutates RBAC: role definitions,
      role assignments, deny assignments, locks) → high.
    - Anything that wildcards a Key Vault secret data action → high.
      ``Microsoft.KeyVault/vaults/secrets/getSecret/action`` alone is
      arguably enough but reviewers can read a single narrow data
      action without help; the wildcard variants are the dangerous
      shape.
    - Everything else → not high. The classifier is deliberately *not*
      trying to score reads — that's the role-detail drawer's job.
    """
    a = action.strip().lower()
    if not a:
        return False
    if a in _HIGH_POWER_EXACT:
        return True
    if a.startswith("microsoft.authorization/") and a.endswith(("/write", "/delete")):
        return True
    return a.startswith("microsoft.keyvault/") and "/secrets/" in a and a.endswith("/*")


def risk_reasons(permissions: list[dict[str, Any]] | None) -> list[str]:
    """Return a short human-readable list of *why* a role is high-power.

    Used by the role-detail drawer to back up the red banner ("high-risk
    because: has ``*``; has Microsoft.Authorization/*/write …"). When
    the role isn't high-risk the list is empty.

    The strings are reviewer-friendly summaries, not the raw action
    strings — multiple matching actions of the same shape collapse to
    one reason so the banner stays short.
    """
    if not permissions:
        return []
    from app.core import entra_roles

    if entra_roles.is_entra(permissions):
        level, gives = entra_roles.level_of(permissions)
        if level == "access":
            return [f"can change who has access in Entra ID ({', '.join(gives)})"]
        if level == "keys":
            return [f"can get in as someone or something else ({', '.join(gives)})"]
        return []
    seen: dict[str, None] = {}
    for perm in permissions:
        for key in ("actions", "dataActions"):
            for action in perm.get(key) or []:
                a = action.strip().lower()
                if not a:
                    continue
                reason: str | None = None
                if a == "*":
                    reason = "grants `*` — every action on every resource"
                elif a == "*/write":
                    reason = "grants `*/write` — write to every resource type"
                elif a == "*/delete":
                    reason = "grants `*/delete` — delete every resource type"
                elif a == "*/action":
                    reason = "grants `*/action` — invoke every resource action"
                elif a.startswith("microsoft.authorization/") and a.endswith(("/write", "/delete")):
                    reason = (
                        "mutates RBAC (`Microsoft.Authorization/*/write|delete`) "
                        "— can grant or revoke other principals' access"
                    )
                elif a.startswith("microsoft.keyvault/") and "/secrets/" in a and a.endswith("/*"):
                    reason = (
                        "wildcards Key Vault secret data actions "
                        "(`Microsoft.KeyVault/*/secrets/*`) — read or write any secret"
                    )
                if reason is not None:
                    seen[reason] = None
    return list(seen.keys())


def classify_permissions(permissions: list[dict[str, Any]] | None) -> str | None:
    """Return ``"high"`` if any permission block grants broad mutating
    power, otherwise ``None``.

    ``permissions`` follows the shape we persist in
    ``role_definitions.permissions`` — a list of dicts with
    ``actions`` / ``notActions`` / ``dataActions`` / ``notDataActions``
    keys. Both ``actions`` and ``dataActions`` are considered: a role
    can be high-power via either plane.

    A ``notActions`` list could in principle *remove* a wildcard's
    power, but in practice every built-in / custom role we've seen
    follows the pattern "broad action + narrow exclusion", and the
    chip is meant to draw a reviewer's *attention*, not encode
    Azure's evaluation precedence. So we don't try to subtract
    notActions.
    """
    if not permissions:
        return None
    from app.core import entra_roles

    if entra_roles.is_entra(permissions):
        return "high" if entra_roles.is_high_risk(permissions) else None
    for perm in permissions:
        for key in ("actions", "dataActions"):
            for action in perm.get(key) or []:
                if _is_high_power_action(action):
                    return "high"
    return None
