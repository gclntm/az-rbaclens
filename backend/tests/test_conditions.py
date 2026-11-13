"""Role-assignment conditions in plain words — the two real texts from the
dev tenant plus the other portal templates."""

from __future__ import annotations

from app.core.conditions import summarise

# Both spellings Azure produced for the same "all except admin roles" template.
COMPACT = (
    "((!(ActionMatches {'Microsoft.Authorization/roleAssignments/write'})) || "
    "(@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAllValues:GuidNotEquals "
    "{8e3af657a8ff443ca75c2fe8c4bcb635, 18d7d88dd35e4fb5a5c37773c20a72d9, f58310d9a9f6439a9e8df62e7b41a168})) && "
    "((!(ActionMatches {'Microsoft.Authorization/roleAssignments/delete'})) || "
    "(@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAllValues:GuidNotEquals "
    "{8e3af657a8ff443ca75c2fe8c4bcb635, 18d7d88dd35e4fb5a5c37773c20a72d9, f58310d9a9f6439a9e8df62e7b41a168}))"
)
VERBOSE = (
    "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR "
    "(@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAllValues:GuidNotEquals "
    "{8e3af657-a8ff-443c-a75c-2fe8c4bcb635, 18d7d88d-d35e-4fb5-a5c3-7773c20a72d9, f58310d9-a9f6-439a-9e8d-f62e7b41a168})) AND "
    "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR "
    "(@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAllValues:GuidNotEquals "
    "{8e3af657-a8ff-443c-a75c-2fe8c4bcb635, 18d7d88d-d35e-4fb5-a5c3-7773c20a72d9, f58310d9-a9f6-439a-9e8d-f62e7b41a168}))"
)
EXPECTED_EXCEPT = "Can grant any role except Owner, User Access Administrator and Role Based Access Control Administrator"


def test_both_real_spellings_read_the_same():
    for text in (COMPACT, VERBOSE):
        s = summarise(text)
        assert s.kind == "delegation"
        assert s.text == EXPECTED_EXCEPT
        assert s.blocks_admin_roles
        assert s.except_roles == [
            "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
            "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",
            "f58310d9-a9f6-439a-9e8d-f62e7b41a168",
        ]


def test_only_these_roles_and_principal_types():
    cond = (
        "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR "
        "(@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals "
        "{acdd72a7-3385-48ef-bd42-f606fba81ae7, 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1} AND "
        "@Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAllValues:StringEqualsIgnoreCase "
        "{'ServicePrincipal'}))"
    )
    s = summarise(
        cond,
        {
            "ACDD72A7-3385-48EF-BD42-F606FBA81AE7": "Reader",
            "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1": "Storage Blob Data Reader",
        },
    )
    assert (
        s.text == "Can only grant Reader and Storage Blob Data Reader, only to service principals"
    )
    assert s.blocks_admin_roles  # neither allowed role can hand out access


def test_only_owner_does_not_block_admin_roles():
    cond = (
        "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals "
        "{8e3af657-a8ff-443c-a75c-2fe8c4bcb635}"
    )
    s = summarise(cond)
    assert s.text == "Can only grant Owner" and not s.blocks_admin_roles


def test_storage_templates():
    cond = (
        "((!(ActionMatches{'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'})) OR "
        "(@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringEquals 'invoices' "
        "AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags:Project<$key_case_sensitive$>] "
        "StringEquals 'Cascade'))"
    )
    s = summarise(cond)
    assert (s.kind, s.text) == (
        "storage",
        "Only container 'invoices', blobs tagged Project=Cascade",
    )


def test_unrecognised_conditions_are_flagged_not_ignored():
    s = summarise("@Environment[UtcNow] DateTimeGreaterThan '2026-01-01T00:00:00Z'")
    assert (s.kind, s.text) == ("other", "Limited by a condition, check it")
    assert summarise(None) is None and summarise("  ") is None
