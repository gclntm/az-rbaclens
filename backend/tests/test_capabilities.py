"""Five-level role classification, pinned with Azure's real role definitions
(as cached in role_definitions.permissions)."""

from __future__ import annotations

import pytest

from app.core.capabilities import Capability, classify


def _p(actions=(), not_actions=(), data=(), not_data=()):
    return [
        {
            "actions": list(actions),
            "notActions": list(not_actions),
            "dataActions": list(data),
            "notDataActions": list(not_data),
        }
    ]


OWNER = _p(["*"])
CONTRIBUTOR = _p(
    ["*"],
    [
        "Microsoft.Authorization/*/Delete",
        "Microsoft.Authorization/*/Write",
        "Microsoft.Authorization/elevateAccess/Action",
    ],
)
UAA = _p(["*/read", "Microsoft.Authorization/*", "Microsoft.Support/*"])
FOUNDRY_PM = _p(
    [
        "Microsoft.Authorization/roleAssignments/write",
        "Microsoft.CognitiveServices/accounts/projects/*",
    ],
    data=["Microsoft.CognitiveServices/*"],
)
READER_AND_DATA_ACCESS = _p(
    [
        "Microsoft.Storage/storageAccounts/listKeys/action",
        "Microsoft.Storage/storageAccounts/ListAccountSas/action",
        "Microsoft.Storage/storageAccounts/read",
    ]
)
AKS_CONTRIBUTOR = _p(
    [
        "Microsoft.Authorization/*/read",
        "Microsoft.ContainerService/managedClusters/*",
        "Microsoft.Resources/deployments/*",
        "Microsoft.Resources/subscriptions/resourceGroups/read",
    ]
)
NETWORK_CONTRIBUTOR = _p(
    [
        "Microsoft.Authorization/*/read",
        "Microsoft.Insights/alertRules/*",
        "Microsoft.Network/*",
        "Microsoft.Resources/deployments/*",
        "Microsoft.Support/*",
    ]
)
SECURITY_ADMIN = _p(
    [
        "Microsoft.Authorization/*/read",
        "Microsoft.Authorization/policyAssignments/*",
        "Microsoft.Security/*",
        "Microsoft.Support/*",
    ]
)
BLOB_OWNER = _p(
    [
        "Microsoft.Storage/storageAccounts/blobServices/containers/*",
        "Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action",
    ],
    data=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
)
BLOB_READER = _p(
    [
        "Microsoft.Storage/storageAccounts/blobServices/containers/read",
        "Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action",
    ],
    data=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
)
FILE_PRIV_READER = _p(
    data=[
        "Microsoft.Storage/storageAccounts/fileServices/fileshares/files/read",
        "Microsoft.Storage/storageAccounts/fileServices/readFileBackupSemantics/action",
    ]
)
KV_SECRETS_USER = _p(
    ["Microsoft.Authorization/*/read"],
    data=[
        "Microsoft.KeyVault/vaults/secrets/getSecret/action",
        "Microsoft.KeyVault/vaults/secrets/readMetadata/action",
    ],
)
READER = _p(["*/read"])
COST_READER = _p(
    ["Microsoft.Consumption/*/read", "Microsoft.CostManagement/*/read", "Microsoft.Support/*"]
)
SUPPORT_CONTRIBUTOR = _p(["Microsoft.Authorization/*/read", "Microsoft.Support/*"])
ACR_PULL = _p(["Microsoft.ContainerRegistry/registries/pull/read"])


def _level(permissions):
    c = classify(permissions)
    return Capability(c.level, c.data, c.data_write)


@pytest.mark.parametrize(
    "permissions, expected",
    [
        (OWNER, Capability("access")),
        (UAA, Capability("access")),
        (FOUNDRY_PM, Capability("access", data=True, data_write=True)),
        # notActions remove role-assignment writes: Contributor cannot change
        # access — but it can list storage keys.
        (CONTRIBUTOR, Capability("keys")),
        (READER_AND_DATA_ACCESS, Capability("keys")),  # "Reader" in name only
        (AKS_CONTRIBUTOR, Capability("keys")),  # managedClusters/* includes admin credentials
        (KV_SECRETS_USER, Capability("keys", data=True, data_write=False)),
        (NETWORK_CONTRIBUTOR, Capability("change")),
        (SECURITY_ADMIN, Capability("change")),
        (
            BLOB_OWNER,
            Capability("data", data=True, data_write=True),
        ),  # containers/* is data ownership
        (
            BLOB_READER,
            Capability("data", data=True, data_write=False),
        ),  # user delegation key ≠ change
        (FILE_PRIV_READER, Capability("data", data=True, data_write=False)),
        (READER, Capability("view")),
        (COST_READER, Capability("view")),  # Microsoft.Support/* is tickets, not resources
        (SUPPORT_CONTRIBUTOR, Capability("view")),
        (ACR_PULL, Capability("view")),
    ],
)
def test_real_roles(permissions, expected):
    assert _level(permissions) == expected


def test_keys_are_named_in_words():
    # Most consequential first, not alphabetical.
    assert classify(CONTRIBUTOR).keys_for[:3] == (
        "storage account keys",
        "Key Vault secrets",
        "web app settings and secrets",
    )
    assert classify(READER_AND_DATA_ACCESS).keys_for == ("storage account keys",)
    assert classify(AKS_CONTRIBUTOR).keys_for == ("Kubernetes cluster credentials",)
    assert classify(KV_SECRETS_USER).keys_for == ("Key Vault secrets",)
    assert "storage account keys" in classify(OWNER).keys_for  # access level keeps the detail
    assert classify(NETWORK_CONTRIBUTOR).keys_for == ()


def test_missing_definition_is_unknown_never_view():
    assert classify(None).level == "unknown"
    assert classify([]).level == "unknown"


def test_wildcards_and_case_follow_azure():
    # Mixed case and a notAction that only narrows: still a change.
    assert (
        classify(_p(["microsoft.compute/*"], ["Microsoft.Compute/virtualMachines/delete"])).level
        == "change"
    )
    # A notAction that removes the whole pattern leaves only reads.
    assert classify(_p(["Microsoft.Compute/*", "*/read"], ["Microsoft.Compute/*"])).level == "view"
    # A not-data action can cancel the only data action.
    assert (
        classify(
            _p(
                ["*/read"],
                data=["Microsoft.Storage/*/blobs/read"],
                not_data=["Microsoft.Storage/*"],
            )
        ).level
        == "view"
    )
