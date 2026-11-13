"""Role-coverage analyzer.

Phase 3 of the events / least-privilege feature. For each role
assignment in a scan, compare what the role *grants* (its actions
list) with what the principal *did* (operations from Activity Log)
in a time window. Roles with a tiny used/granted ratio are
candidates for narrowing.

Everything in this module is pure: takes plain dicts / lists and
returns the same. The endpoint layer wires it to the database.

Key design choices:

- We work at the *pattern* level, not the expanded action level.
  Azure has tens of thousands of distinct actions; expanding ``*``
  is not feasible and not helpful. Instead, we keep the role's
  declared patterns (`*`, `*/write`, `Microsoft.Storage/*`, …)
  and ask "did at least one observed operation match this pattern".
- ``notActions`` are subtracted from coverage: a granted pattern
  whose matches are all carved out by ``notActions`` doesn't count
  as exercised even if an operation matched the broader pattern.
- ``coverage_pct`` is computed over patterns, not over observed
  operations. "Owner has 1 used out of 1 granted (the ``*``)" is
  100 % even though it observed thousands of distinct operations.
  This is the right shape for least-privilege analysis: a single
  matching action exercises the broad pattern, but the unused
  patterns are what we want to surface.
- Caller correlation is heuristic — see ``correlate_caller``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from typing import Any

from app.core.activity_payload import caller_oid_from_payload

# --------------------------------------------------------------------------------------
# Pattern handling
# --------------------------------------------------------------------------------------


def _flatten_patterns(permissions: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    """Return ``(granted, denied)`` action-pattern sets.

    Combines ``actions`` + ``dataActions`` into ``granted`` and
    ``notActions`` + ``notDataActions`` into ``denied``. Case-folded
    so pattern matching ignores Azure's inconsistent casing.
    """
    granted: set[str] = set()
    denied: set[str] = set()
    for perm in permissions or []:
        for key in ("actions", "dataActions"):
            for action in perm.get(key) or []:
                if isinstance(action, str) and action:
                    granted.add(action.lower())
        for key in ("notActions", "notDataActions"):
            for action in perm.get(key) or []:
                if isinstance(action, str) and action:
                    denied.add(action.lower())
    return granted, denied


def _matches(pattern: str, op: str) -> bool:
    """Wildcard match for one pattern against one observed action.

    Uses ``fnmatch`` so ``*`` matches anything (including slashes —
    Azure RBAC's wildcard semantics, not POSIX globbing). Both
    inputs are case-folded.
    """
    return fnmatch.fnmatchcase(op.lower(), pattern.lower())


def _is_exercised(pattern: str, ops: Iterable[str], denied: set[str]) -> bool:
    """A pattern is "exercised" iff some observed op matches the
    pattern AND is NOT carved out by any notActions pattern.

    Example: Contributor's ``*`` is exercised by any write, but
    its ``notActions`` exclude ``Microsoft.Authorization/*/write``
    — so an Authorization/write observation would NOT credit ``*``.
    (In practice such an observation can't happen because Azure
    would reject the call, but the dedupe stays correct.)
    """
    return any(_matches(pattern, op) and not any(_matches(d, op) for d in denied) for op in ops)


# --------------------------------------------------------------------------------------
# Coverage computation
# --------------------------------------------------------------------------------------


def compute_coverage(
    *,
    permissions: list[dict[str, Any]] | None,
    observed_operations: Iterable[str],
    top_n_unused: int = 5,
) -> dict[str, Any]:
    """Compute pattern-level coverage of a role.

    Returns:

    - ``granted_pattern_count`` — distinct patterns in actions + dataActions.
    - ``used_pattern_count``    — granted patterns exercised by at least one op.
    - ``coverage_pct``          — 0-100 (rounded). Zero when nothing granted.
    - ``unused_patterns``       — up to ``top_n_unused`` granted patterns
                                  that were never matched.
    - ``has_wildcard``          — true if any granted pattern is broader
                                  than a single concrete action. Useful
                                  signal: a wildcarded role with 1/1
                                  coverage may still be over-broad.
    """
    granted, denied = _flatten_patterns(permissions)
    if not granted:
        return {
            "granted_pattern_count": 0,
            "used_pattern_count": 0,
            "coverage_pct": 0,
            "unused_patterns": [],
            "has_wildcard": False,
        }
    ops = [o for o in observed_operations if o]
    used: set[str] = set()
    unused: list[str] = []
    for pattern in sorted(granted):
        if _is_exercised(pattern, ops, denied):
            used.add(pattern)
        else:
            unused.append(pattern)
    pct = round(100 * len(used) / len(granted)) if granted else 0
    return {
        "granted_pattern_count": len(granted),
        "used_pattern_count": len(used),
        "coverage_pct": pct,
        "unused_patterns": unused[:top_n_unused],
        "has_wildcard": any("*" in p for p in granted),
    }


# --------------------------------------------------------------------------------------
# Caller correlation
# --------------------------------------------------------------------------------------


def _ops_covered_by(ops: Iterable[str], granted: set[str], denied: set[str]) -> bool:
    """True iff every op matches at least one granted pattern AND is
    not carved out by any denied pattern.

    Used to test whether a candidate role would have authorized the
    principal's observed activity.
    """
    for op in ops:
        op_l = op.lower()
        if any(_matches(d, op_l) for d in denied):
            # If a candidate's notActions deny this op, the role
            # wouldn't authorize it — fail the candidate.
            return False
        if not any(_matches(g, op_l) for g in granted):
            return False
    return True


def role_covers(
    broader_permissions: list[dict[str, Any]] | None,
    narrower_permissions: list[dict[str, Any]] | None,
) -> bool:
    """True iff ``broader_permissions`` grants everything that
    ``narrower_permissions`` does.

    Used by the Coverage tab's "covered by ancestor" detection: when
    a principal holds an RA at some ancestor scope with a role whose
    actions include the actions of the RA at a descendant scope, the
    descendant RA is redundant.

    Approximation — we work at the pattern level, not at concrete-
    action level (expanding ``*`` is not feasible). For every
    pattern in the narrower role, at least one broader pattern must
    fnmatch it; if any of the broader role's ``notActions`` would
    deny something the narrower role grants, the broader role is
    *not* a superset (Azure carves it out). The check is symmetric
    on Actions and DataActions.

    Empty / missing permissions for either side returns False (we
    can't prove coverage without data).
    """
    if not broader_permissions or not narrower_permissions:
        return False
    broader_granted, broader_denied = _flatten_patterns(broader_permissions)
    narrower_granted, _ = _flatten_patterns(narrower_permissions)
    if not narrower_granted:
        return False

    for np in narrower_granted:
        # The narrower pattern must be subsumed by at least one
        # broader pattern. fnmatch is conservative — ``*`` matches
        # everything, and concrete strings match themselves.
        if not any(fnmatch.fnmatchcase(np, bp) for bp in broader_granted):
            return False
        # If a broader notActions pattern fnmatches the narrower
        # pattern, the broader role would deny something the
        # narrower role allows — so broader is NOT a superset.
        if any(fnmatch.fnmatchcase(np, dp) for dp in broader_denied):
            return False
    return True


def recommend_narrower_roles(
    *,
    observed_operations: Iterable[str],
    current_pattern_count: int,
    candidates: list[dict[str, Any]],
    top_n: int = 3,
    exclude_high_risk: bool = True,
) -> list[dict[str, Any]]:
    """Suggest built-in roles that would cover the observed activity
    with fewer granted patterns than the current role.

    Each candidate dict needs ``role_definition_id``, ``role_name``,
    ``role_type``, and ``permissions`` (same shape as
    ``RoleDefinition.permissions``). The caller filters down to
    built-in roles before calling — we don't recommend swapping a
    custom role for another custom role; that's a re-design, not a
    narrowing.

    The recommendation list is ordered by ``granted_pattern_count``
    ascending (tightest first) then by name for stable tie-breaks.

    Edge cases:

    - Zero observed operations → empty list. We can't recommend a
      narrower role without knowing what the principal needs. The UI
      labels this as "no usage observed" rather than confidently
      suggesting Reader.
    - All candidates broader than the current role → empty list. The
      cap at ``current_pattern_count - 1`` keeps us from suggesting
      a side-grade.
    - ``exclude_high_risk`` (default True): drop candidates that
      themselves classify as ``role_risk == "high"`` via the same
      classifier we use elsewhere. A role like ``Owner-Twin`` whose
      single granted pattern is ``*`` would technically have fewer
      patterns than Contributor — but recommending it as a narrowing
      is absurd. Operators can flip this off if they want side-grade
      candidates surfaced too.
    """
    from app.core.role_risk import classify_permissions

    ops = [o for o in observed_operations if o]
    if not ops:
        return []
    out: list[dict[str, Any]] = []
    for cand in candidates:
        perms = cand.get("permissions")
        granted, denied = _flatten_patterns(perms)
        if not granted:
            continue
        if len(granted) >= current_pattern_count:
            continue
        if exclude_high_risk and classify_permissions(perms) == "high":
            continue
        if _ops_covered_by(ops, granted, denied):
            reduction = current_pattern_count - len(granted)
            out.append(
                {
                    "role_definition_id": cand["role_definition_id"],
                    "role_name": cand.get("role_name"),
                    "role_type": cand.get("role_type"),
                    "granted_pattern_count": len(granted),
                    "patterns_saved": reduction,
                }
            )
    out.sort(
        key=lambda r: (
            r["granted_pattern_count"],
            (r["role_name"] or "").lower(),
        )
    )
    return out[:top_n]


def correlate_caller(
    *,
    raw: dict[str, Any] | None,
    caller: str | None,
    principals: dict[str, dict[str, str | None]],
) -> str | None:
    """Map an event's caller to a known ``principal_id`` (GUID), or
    None if we can't.

    ``principals`` is ``{principal_id: {"name": <upn or display>,
    "type": "User"|"ServicePrincipal"|...}}`` — derived from the
    scan's RAs ahead of time so we don't re-query Graph here.

    Three signals tried in order:

    1. ``raw.claims['http://schemas.microsoft.com/identity/claims/objectidentifier']``
       (or short form ``oid``) — when present, an exact GUID match.
    2. ``caller`` matches a known principal's ``name`` exactly
       (case-insensitive). Covers UPN-equality for users.
    3. ``caller`` is a GUID that matches a principal_id directly.

    Returns the principal_id (lowercased) or None. Tests pin all
    three paths.
    """
    # Path 1: JWT objectidentifier in claims (full or slimmed payload).
    guid = caller_oid_from_payload(raw if isinstance(raw, dict) else None)
    if guid and guid in principals:
        return guid

    if not caller:
        return None
    needle = caller.strip().lower()
    if not needle:
        return None

    # Path 2: name match (UPN or display name as we resolved it).
    for pid, info in principals.items():
        name = (info.get("name") or "").lower()
        if name and name == needle:
            return pid

    # Path 3: caller IS the principal_id GUID.
    if needle in principals:
        return needle

    return None
