"""What an Activity Log collection can target.

Azure keeps one Activity Log per subscription and one per management group.
A collection (``activity_pulls``, and the ``activity_pull`` schedules) is
keyed by a subscription GUID, or by :data:`MGMT_GROUPS` for the logs of all
management groups together: they hold only what was done on a group itself
(access given or removed there, policies, moves), a few events a month, so
one collection reads them all. Their events are stored under the same key
in ``activity_events.subscription_id``.
"""

from __future__ import annotations

MGMT_GROUPS = "management-groups"
MGMT_GROUPS_NAME = "Management groups"
# Lowercased ARM prefix of every management-group place and resource.
MGMT_GROUP_PREFIX = "/providers/microsoft.management/managementgroups/"


def is_mgmt_groups(target: str | None) -> bool:
    return (target or "").strip().lower() == MGMT_GROUPS
