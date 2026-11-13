"""What a role lets someone *do*, in five plain-language levels.

Used by the scope page's "Who can do what" tab. A role definition is
classified by evaluating its permissions the way Azure does — ``actions``
minus ``notActions``, ``dataActions`` minus ``notDataActions`` — then
placed at the most powerful level it reaches:

1. ``access`` — can change who has access (``roleAssignments/write``):
   can grant anyone, including itself, everything below.
2. ``keys``   — can fetch keys, credentials or secrets (storage account
   keys, AKS admin credentials, Key Vault secrets, …). Those unlock
   everything inside a resource and keep working after the role is
   removed — why Contributor lands here, not under "change".
3. ``change`` — can create, reconfigure or delete resources.
4. ``data``   — works on the contents of storage / services (blob, file,
   queue data, …) without changing the resources; ``data_write`` tells
   reading from changing.
5. ``view``   — reads configuration only.

``unknown`` is for roles whose definition we never cached — never
shown as the safest level.

Deliberately not counted as a change: ``Microsoft.Support/*`` (support
tickets), ``Microsoft.Resources/deployments/*`` (a deployment can only
create what the caller may already create), and read-like ``…/action``
verbs (``list*``, ``get*``, ``generateUserDelegationKey`` …). Container
management under a storage service (``blobServices/containers/*``)
belongs to the data level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Literal

Level = Literal["access", "keys", "change", "data", "view", "unknown"]

# Most powerful first; ``unknown`` sorts after ``view`` but is flagged, not trusted.
LEVELS: tuple[Level, ...] = ("access", "keys", "change", "data", "view", "unknown")
LEVEL_RANK: dict[str, int] = {lvl: i for i, lvl in enumerate(LEVELS)}

ACCESS_ACTION = "Microsoft.Authorization/roleAssignments/write"

# Concrete actions that hand out keys, connection strings or credentials,
# with the words the UI uses for them — in order of how much they unlock,
# which is also the order ``keys_for`` lists them.
KEY_ACTIONS: dict[str, str] = {
    "Microsoft.Storage/storageAccounts/listKeys/action": "storage account keys",
    "Microsoft.Storage/storageAccounts/regenerateKey/action": "storage account keys",
    "Microsoft.Storage/storageAccounts/ListAccountSas/action": "storage account keys",
    # Key Vault with access policies: whoever can write them can grant
    # themselves the secrets.
    "Microsoft.KeyVault/vaults/accessPolicies/write": "Key Vault secrets",
    "Microsoft.Web/sites/config/list/action": "web app settings and secrets",
    "Microsoft.Web/sites/publishxml/action": "web app publishing credentials",
    "Microsoft.CognitiveServices/accounts/listKeys/action": "AI service keys",
    "Microsoft.ContainerService/managedClusters/listClusterAdminCredential/action": "Kubernetes cluster credentials",
    "Microsoft.ContainerService/managedClusters/listClusterUserCredential/action": "Kubernetes cluster credentials",
    "Microsoft.ContainerRegistry/registries/listCredentials/action": "container registry credentials",
    "Microsoft.DocumentDB/databaseAccounts/listKeys/action": "Cosmos DB keys",
    "Microsoft.DocumentDB/databaseAccounts/listConnectionStrings/action": "Cosmos DB keys",
    "Microsoft.EventHub/namespaces/authorizationRules/listKeys/action": "Event Hubs keys",
    "Microsoft.ServiceBus/namespaces/authorizationRules/listKeys/action": "Service Bus keys",
    "Microsoft.Relay/namespaces/authorizationRules/listKeys/action": "Relay keys",
    "Microsoft.Cache/redis/listKeys/action": "Redis keys",
    "Microsoft.Search/searchServices/listAdminKeys/action": "search admin keys",
    "Microsoft.OperationalInsights/workspaces/sharedKeys/action": "Log Analytics workspace keys",
    "Microsoft.Automation/automationAccounts/listKeys/action": "Automation account keys",
    "Microsoft.Batch/batchAccounts/listKeys/action": "Batch account keys",
    "Microsoft.Devices/IotHubs/listkeys/action": "IoT Hub keys",
    "Microsoft.SignalRService/SignalR/listkeys/action": "SignalR keys",
    "Microsoft.Maps/accounts/listKeys/action": "Maps keys",
}
SECRET_DATA_ACTIONS: dict[str, str] = {
    "Microsoft.KeyVault/vaults/secrets/getSecret/action": "Key Vault secrets",
}
_KEY_ORDER = tuple(dict.fromkeys([*KEY_ACTIONS.values(), *SECRET_DATA_ACTIONS.values()]))

# Action patterns that never make a role a "change" role.
_IGNORED_FOR_CHANGE = ("microsoft.support/", "microsoft.resources/deployments/")
# Storage sub-resources that belong to the data plane's owner.
_DATA_CONTAINERS = re.compile(
    r"^microsoft\.storage/storageaccounts/"
    r"(blobservices/containers|fileservices/fileshares|queueservices/queues|tableservices/tables)/"
)
_READLIKE_VERB = re.compile(
    r"^(list|get|read|query|check|validate|search|generateuserdelegationkey)"
)


@dataclass(frozen=True)
class Capability:
    level: Level
    # Has any data-plane permission (``dataActions``).
    data: bool = False
    # Data permissions include writing or deleting, not only reading.
    data_write: bool = False
    # For ``keys`` (and above): what it can fetch, in words, e.g.
    # ("Kubernetes cluster credentials", "storage account keys").
    keys_for: tuple[str, ...] = ()


UNKNOWN = Capability("unknown")


def _blocks(permissions: Any) -> list[dict[str, list[str]]]:
    return [b for b in (permissions or []) if isinstance(b, dict)]


def _allowed(blocks, action: str, key: str = "actions", not_key: str = "notActions") -> bool:
    """Does ``action`` survive ``key`` minus ``not_key`` (Azure's wildcard, case-insensitive)?"""
    a = action.lower()
    return any(
        any(fnmatchcase(a, p.lower()) for p in b.get(key, []))
        and not any(fnmatchcase(a, n.lower()) for n in b.get(not_key, []))
        for b in blocks
    )


def _granted_patterns(blocks, key: str, not_key: str) -> list[str]:
    """Patterns in ``key`` not wholly removed by a ``not_key`` pattern."""
    out = []
    for b in blocks:
        negations = [n.lower() for n in b.get(not_key, [])]
        for p in b.get(key, []):
            lp = p.lower()
            if not any(fnmatchcase(lp, n) for n in negations):
                out.append(lp)
    return out


def _is_change(pattern: str) -> bool:
    if pattern.endswith("/read"):
        return False
    if pattern.startswith(_IGNORED_FOR_CHANGE) or _DATA_CONTAINERS.match(pattern):
        return False
    if pattern.endswith("/action"):
        verb = pattern[: -len("/action")].rsplit("/", 1)[-1]
        if _READLIKE_VERB.match(verb):
            return False
    return True


def _is_data_write(pattern: str) -> bool:
    if pattern.endswith("/read"):
        return False
    if pattern.endswith("/action"):
        verb = pattern[: -len("/action")].rsplit("/", 1)[-1]
        return not _READLIKE_VERB.match(verb)
    return True


def classify(permissions: Any) -> Capability:
    """Classify one role definition's ``permissions`` (as cached from Azure)."""
    blocks = _blocks(permissions)
    if not blocks:
        return UNKNOWN
    data_patterns = _granted_patterns(blocks, "dataActions", "notDataActions")
    data = bool(data_patterns)
    data_write = any(_is_data_write(p) for p in data_patterns)

    granted = [label for a, label in KEY_ACTIONS.items() if _allowed(blocks, a)] + [
        label
        for a, label in SECRET_DATA_ACTIONS.items()
        if _allowed(blocks, a, "dataActions", "notDataActions")
    ]
    keys_for = tuple(dict.fromkeys(label for label in _KEY_ORDER if label in granted))

    def cap(level: Level) -> Capability:
        return Capability(level, data, data_write, keys_for if LEVEL_RANK[level] <= 1 else ())

    if _allowed(blocks, ACCESS_ACTION):
        return cap("access")
    if keys_for:
        return cap("keys")
    if any(_is_change(p) for p in _granted_patterns(blocks, "actions", "notActions")):
        return cap("change")
    if data:
        return cap("data")
    return cap("view")
