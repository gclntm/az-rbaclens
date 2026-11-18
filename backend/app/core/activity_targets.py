"""What an Activity Log collection can target.

Azure keeps one Activity Log per subscription and one per management group.
A collection (``activity_pulls``, and the ``activity_pull`` schedules) is
keyed by a subscription GUID, or by :data:`MGMT_GROUPS` for the logs of all
management groups together: they hold only what was done on a group itself
(access given or removed there, policies, moves), a few events a month, so
one collection reads them all. Their events are stored under the same key
in ``activity_events.subscription_id``.

:data:`ENTRA_AUDIT` is the third kind of target: the Entra ID audit log,
read through Microsoft Graph (``tools/entra_audit.py``). Only its group
membership events are kept, in ``directory_audit_events``; they say who
added or removed a member of a group that holds access. It is not an Azure
Activity Log: it never feeds the usage findings or the Activity Log page.
"""

from __future__ import annotations

MGMT_GROUPS = "management-groups"
MGMT_GROUPS_NAME = "Management groups"
# Lowercased ARM prefix of every management-group place and resource.
MGMT_GROUP_PREFIX = "/providers/microsoft.management/managementgroups/"


def is_mgmt_groups(target: str | None) -> bool:
    return (target or "").strip().lower() == MGMT_GROUPS


ENTRA_AUDIT = "entra-directory"
ENTRA_AUDIT_NAME = "Entra ID audit log"


def is_entra_audit(target: str | None) -> bool:
    return (target or "").strip().lower() == ENTRA_AUDIT


def is_subscription_log(target: str | None) -> bool:
    """A subscription's Activity Log, not one of the special targets."""
    return not (is_mgmt_groups(target) or is_entra_audit(target))
