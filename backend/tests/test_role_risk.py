"""Tests for the role-power risk classifier."""

from __future__ import annotations

import pytest

from app.core.role_risk import classify_permissions


def _perm(actions=None, not_actions=None, data_actions=None, not_data_actions=None):
    return {
        "actions": list(actions or []),
        "notActions": list(not_actions or []),
        "dataActions": list(data_actions or []),
        "notDataActions": list(not_data_actions or []),
    }


@pytest.mark.parametrize(
    "actions",
    [
        ["*"],
        ["*/write"],
        ["*/delete"],
        ["*/action"],
        ["Microsoft.Storage/storageAccounts/read", "*"],  # mixed reads + wildcard
    ],
)
def test_wildcard_actions_are_high(actions):
    assert classify_permissions([_perm(actions=actions)]) == "high"


def test_owner_like_role_is_high():
    """Owner is ``*`` minus a few notActions."""
    perm = _perm(actions=["*"], not_actions=["Microsoft.Authorization/*/Delete"])
    assert classify_permissions([perm]) == "high"


def test_contributor_like_role_is_high():
    """Contributor is ``*`` with Microsoft.Authorization/* carved out."""
    perm = _perm(
        actions=["*"],
        not_actions=[
            "Microsoft.Authorization/*/Delete",
            "Microsoft.Authorization/*/Write",
        ],
    )
    assert classify_permissions([perm]) == "high"


def test_user_access_administrator_is_high_via_auth_writes():
    """UAA grants ``Microsoft.Authorization/*`` actions explicitly."""
    perm = _perm(
        actions=[
            "*/read",
            "Microsoft.Authorization/*/Read",
            "Microsoft.Authorization/*/Write",
            "Microsoft.Support/*",
        ]
    )
    assert classify_permissions([perm]) == "high"


def test_reader_is_not_high():
    perm = _perm(actions=["*/read"])
    assert classify_permissions([perm]) is None


def test_narrow_writer_is_not_high():
    """A role limited to one resource provider's writes isn't high-power."""
    perm = _perm(
        actions=[
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Storage/storageAccounts/listKeys/action",
        ]
    )
    assert classify_permissions([perm]) is None


def test_keyvault_secret_wildcard_data_action_is_high():
    perm = _perm(
        data_actions=["Microsoft.KeyVault/vaults/secrets/*"],
    )
    assert classify_permissions([perm]) == "high"


def test_keyvault_single_secret_read_data_action_is_not_high():
    """A single getSecret action is narrow enough to not warrant a chip."""
    perm = _perm(
        data_actions=["Microsoft.KeyVault/vaults/secrets/getSecret/action"],
    )
    assert classify_permissions([perm]) is None


def test_empty_or_missing_permissions_returns_none():
    assert classify_permissions(None) is None
    assert classify_permissions([]) is None
    assert classify_permissions([_perm()]) is None


def test_case_insensitive():
    assert (
        classify_permissions([_perm(actions=["MICROSOFT.AUTHORIZATION/ROLEASSIGNMENTS/WRITE"])])
        == "high"
    )
    assert classify_permissions([_perm(actions=["*/Write"])]) == "high"


def test_multiple_permission_blocks_short_circuit_on_any_high():
    """Custom roles can have multiple permission blocks. Any one high is enough."""
    perms = [
        _perm(actions=["*/read"]),
        _perm(actions=["Microsoft.Compute/virtualMachines/start/action"]),
        _perm(actions=["*"]),
    ]
    assert classify_permissions(perms) == "high"


def test_data_action_wildcard_outside_keyvault_is_not_specifically_flagged():
    """Generic data wildcards on other providers don't trigger the KV rule.

    They'd be caught by ``*`` in actions if present, but ``dataActions``
    alone for, say, storage blobs read/write isn't an automatic high.
    Reviewers can use the role-detail drawer for nuance there.
    """
    perm = _perm(
        data_actions=[
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
        ]
    )
    assert classify_permissions([perm]) is None
