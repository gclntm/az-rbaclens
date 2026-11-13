"""PIM (Privileged Identity Management) during a snapshot.

Two reads per queried scope, both read-only:

- **eligible** assignments (``roleEligibilityScheduleInstances``): roles a
  principal may activate on demand. They never appear in
  ``role_assignments`` — the principal holds nothing until activation —
  so without this step "eligible for Owner" is invisible;
- **active** schedule instances (``roleAssignmentScheduleInstances``): for
  each role assignment, ``Assigned`` (standing) or ``Activated`` (an
  eligible role switched on) and when it ends. Linked to assignments by
  ``origin_role_assignment_id``.

An unfiltered list at a subscription returns everything at, above and
below it; at a management group it does not reach into subscriptions. So
the snapshot's root is queried, plus every subscription it walked, and
results are deduped by id.

PIM is never allowed to fail a snapshot: errors are recorded on
``PimResult`` (→ ``scans.eligibility_status`` / ``eligibility_error``) and
the role-assignment result still lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from azure.core.credentials import TokenCredential
from azure.mgmt.authorization import AuthorizationManagementClient

from app.core.azure import get_azure_transport
from app.db.models import RoleEligibility

logger = logging.getLogger(__name__)

# Any GUID satisfies the client constructor; ``list_for_scope`` takes the
# scope from its argument.
_ANY_SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"


@dataclass
class PimResult:
    # ``read`` | ``failed`` | None (not attempted).
    status: str | None = None
    error: str | None = None
    eligibilities: list[RoleEligibility] = field(default_factory=list)
    # Role-assignment GUID (last path segment, lowercased) → (type, end).
    active: dict[str, tuple[str | None, datetime | None]] = field(default_factory=dict)

    @classmethod
    def not_attempted(cls) -> PimResult:
        return cls()


def _guid(arm_id: str | None) -> str | None:
    return arm_id.rstrip("/").rsplit("/", 1)[-1].lower() if arm_id else None


def query_scopes(root_scope: str, root_level: str, hierarchy: dict[str, Any]) -> list[str]:
    """The snapshot root, plus every subscription walked under a management group."""
    scopes = [root_scope]
    if root_level == "mgmt_group":
        scopes += sorted(s for s, node in hierarchy.items() if node.get("level") == "subscription")
    return scopes


def collect_pim(
    root_scope: str,
    root_level: str,
    hierarchy: dict[str, Any],
    credential: TokenCredential,
    role_info_cache: dict[str, dict[str, Any]],
) -> PimResult:
    from app.tools.scan_list import role_info
    from app.tools.scope_ids import level_from_scope, normalize_scope

    sub = next(
        (s.rsplit("/", 1)[-1] for s, n in hierarchy.items() if n.get("level") == "subscription"),
        _ANY_SUBSCRIPTION,
    )
    client = AuthorizationManagementClient(credential, sub, transport=get_azure_transport())
    scopes = query_scopes(root_scope, root_level, hierarchy)
    result = PimResult()
    seen: set[str] = set()
    failures: list[str] = []

    for scope in scopes:
        try:
            eligible = list(client.role_eligibility_schedule_instances.list_for_scope(scope))
            active = list(client.role_assignment_schedule_instances.list_for_scope(scope))
        except Exception as exc:  # noqa: BLE001 - one failing scope must not abort the scan
            logger.warning("PIM read failed at %s: %s", scope, exc)
            failures.append(f"{scope}: {type(exc).__name__}")
            continue
        for inst in active:
            ra = _guid(getattr(inst, "origin_role_assignment_id", None))
            if ra:
                result.active[ra] = (
                    getattr(inst, "assignment_type", None),
                    getattr(inst, "end_date_time", None),
                )
        for inst in eligible:
            iid = getattr(inst, "id", None) or ""
            if not iid or iid.lower() in seen:
                continue
            seen.add(iid.lower())
            inst_scope = normalize_scope(getattr(inst, "scope", None) or scope)
            role_name, role_type = role_info(client, inst.role_definition_id, role_info_cache)
            node = hierarchy.get(inst_scope) or {}
            result.eligibilities.append(
                RoleEligibility(
                    azure_id=iid[:255],
                    scope=inst_scope,
                    scope_name=node.get("name") or inst_scope.rsplit("/", 1)[-1],
                    scope_level=level_from_scope(inst_scope),
                    principal_id=(inst.principal_id or "").lower(),
                    principal_name=None,  # resolved with the snapshot's other names
                    principal_type=getattr(inst, "principal_type", None) or "Unknown",
                    role_definition_id=inst.role_definition_id,
                    role_name=role_name,
                    role_type=role_type,
                    member_type=getattr(inst, "member_type", None),
                    condition=getattr(inst, "condition", None) or None,
                    condition_version=getattr(inst, "condition_version", None) or None,
                    start_at=getattr(inst, "start_date_time", None),
                    end_at=getattr(inst, "end_date_time", None),
                    status=getattr(inst, "status", None),
                )
            )

    if failures and len(failures) == len(scopes):
        result.status = "failed"
        result.error = "PIM could not be read: " + "; ".join(failures[:3])
    else:
        result.status = "read"
        if failures:
            result.error = (
                f"PIM could not be read at {len(failures)} of {len(scopes)} scopes: "
                + "; ".join(failures[:3])
            )
    return result
