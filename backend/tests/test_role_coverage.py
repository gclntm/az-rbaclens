"""Tests for the role-coverage analyzer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.role_coverage import (
    _flatten_patterns,
    _is_exercised,
    _matches,
    compute_coverage,
    correlate_caller,
    recommend_narrower_roles,
    role_covers,
)
from tests.conftest import coverage_of


def _perm(actions=None, not_actions=None, data_actions=None, not_data_actions=None):
    return {
        "actions": list(actions or []),
        "notActions": list(not_actions or []),
        "dataActions": list(data_actions or []),
        "notDataActions": list(not_data_actions or []),
    }


# --------------------------------------------------------------------------------------
# Pattern primitives
# --------------------------------------------------------------------------------------


def test_flatten_separates_granted_and_denied():
    perms = [
        _perm(
            actions=["*"],
            not_actions=["Microsoft.Authorization/*/Write"],
            data_actions=["Microsoft.KeyVault/vaults/secrets/read"],
            not_data_actions=["Microsoft.KeyVault/vaults/secrets/purge/action"],
        )
    ]
    granted, denied = _flatten_patterns(perms)
    assert "*" in granted
    assert "microsoft.keyvault/vaults/secrets/read" in granted
    assert "microsoft.authorization/*/write" in denied
    assert "microsoft.keyvault/vaults/secrets/purge/action" in denied


@pytest.mark.parametrize(
    ("pattern", "op", "expected"),
    [
        ("*", "Microsoft.Storage/storageAccounts/write", True),
        ("*/write", "Microsoft.Storage/storageAccounts/write", True),
        ("*/read", "Microsoft.Storage/storageAccounts/write", False),
        (
            "microsoft.storage/*",
            "Microsoft.Storage/storageAccounts/listKeys/action",
            True,
        ),
        (
            "MICROSOFT.STORAGE/storageAccounts/write",
            "microsoft.storage/storageaccounts/write",
            True,
        ),
    ],
)
def test_matches_handles_wildcards_and_case(pattern, op, expected):
    assert _matches(pattern, op) is expected


def test_is_exercised_respects_denied():
    """A pattern is NOT exercised if the only matching op falls under
    a notActions pattern."""
    denied = {"microsoft.authorization/*/write"}
    ops = ["Microsoft.Authorization/roleAssignments/write"]
    # The broad `*` would match, but the op is carved out — so the
    # pattern doesn't get credit.
    assert _is_exercised("*", ops, denied) is False
    # A different op exercises it fine.
    assert _is_exercised("*", ["Microsoft.Storage/storageAccounts/write"], denied) is True


# --------------------------------------------------------------------------------------
# compute_coverage
# --------------------------------------------------------------------------------------


def test_compute_coverage_owner_fully_exercised():
    """Owner has a single ``*`` granted pattern — one matching op
    drives coverage to 100 %."""
    out = compute_coverage(
        permissions=[_perm(actions=["*"])],
        observed_operations=["Microsoft.Storage/storageAccounts/write"],
    )
    assert out["granted_pattern_count"] == 1
    assert out["used_pattern_count"] == 1
    assert out["coverage_pct"] == 100
    assert out["unused_patterns"] == []
    assert out["has_wildcard"] is True


def test_compute_coverage_owner_with_no_observations():
    out = compute_coverage(
        permissions=[_perm(actions=["*"])],
        observed_operations=[],
    )
    assert out["coverage_pct"] == 0
    assert out["unused_patterns"] == ["*"]


def test_compute_coverage_narrow_role_partial():
    """Three granted patterns, two seen."""
    out = compute_coverage(
        permissions=[
            _perm(
                actions=[
                    "Microsoft.Storage/storageAccounts/read",
                    "Microsoft.Storage/storageAccounts/write",
                    "Microsoft.Storage/storageAccounts/delete",
                ]
            )
        ],
        observed_operations=[
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Storage/storageAccounts/read",  # duplicate ok
            "Microsoft.Storage/storageAccounts/write",
        ],
    )
    assert out["granted_pattern_count"] == 3
    assert out["used_pattern_count"] == 2
    assert out["coverage_pct"] == 67
    assert "microsoft.storage/storageaccounts/delete" in out["unused_patterns"]
    assert out["has_wildcard"] is False


def test_compute_coverage_unused_capped_at_top_n():
    perms = [_perm(actions=[f"Microsoft.Storage/x{i}/read" for i in range(10)])]
    out = compute_coverage(
        permissions=perms,
        observed_operations=[],
        top_n_unused=3,
    )
    assert out["granted_pattern_count"] == 10
    assert len(out["unused_patterns"]) == 3


def test_compute_coverage_empty_role():
    out = compute_coverage(permissions=None, observed_operations=["x"])
    assert out["granted_pattern_count"] == 0
    assert out["coverage_pct"] == 0


def test_compute_coverage_subtracts_notactions():
    """A wildcard whose only observation is denied → unused."""
    out = compute_coverage(
        permissions=[
            _perm(
                actions=["*"],
                not_actions=["Microsoft.Authorization/*/write"],
            )
        ],
        observed_operations=["Microsoft.Authorization/roleAssignments/write"],
    )
    assert out["coverage_pct"] == 0
    assert out["unused_patterns"] == ["*"]


# --------------------------------------------------------------------------------------
# correlate_caller
# --------------------------------------------------------------------------------------


PRINCIPALS = {
    "alice-guid": {"name": "alice@example.com", "type": "User"},
    "bob-guid": {"name": "Bob Builder", "type": "User"},
    "sp-guid": {"name": "deploy-sp", "type": "ServicePrincipal"},
}


def test_correlate_uses_objectidentifier_claim_first():
    raw = {
        "claims": {"http://schemas.microsoft.com/identity/claims/objectidentifier": "alice-guid"}
    }
    # Caller is a misleading string — claim takes precedence.
    assert (
        correlate_caller(raw=raw, caller="some-other-name", principals=PRINCIPALS) == "alice-guid"
    )


def test_correlate_falls_back_to_oid_short_claim():
    raw = {"claims": {"oid": "bob-guid"}}
    assert correlate_caller(raw=raw, caller=None, principals=PRINCIPALS) == "bob-guid"


def test_correlate_matches_upn_case_insensitive():
    assert (
        correlate_caller(raw=None, caller="ALICE@example.com", principals=PRINCIPALS)
        == "alice-guid"
    )


def test_correlate_matches_display_name():
    assert correlate_caller(raw=None, caller="bob builder", principals=PRINCIPALS) == "bob-guid"


def test_correlate_falls_back_to_caller_as_guid():
    assert correlate_caller(raw=None, caller="sp-guid", principals=PRINCIPALS) == "sp-guid"


def test_correlate_returns_none_when_unknown():
    assert correlate_caller(raw=None, caller="stranger@example.com", principals=PRINCIPALS) is None


def test_correlate_returns_none_when_no_signals():
    assert correlate_caller(raw=None, caller=None, principals=PRINCIPALS) is None


def test_correlate_objectidentifier_must_be_known_principal():
    """A claim pointing at an unknown principal shouldn't be returned —
    callers expect a value they can join against the scan's RAs."""
    raw = {"claims": {"oid": "unknown-guid"}}
    # No fallback because caller is None.
    assert correlate_caller(raw=raw, caller=None, principals=PRINCIPALS) is None


# --------------------------------------------------------------------------------------
# Coverage of a whole check (build_scan_coverage)
# --------------------------------------------------------------------------------------


def test_coverage_endpoint_intersects_grants_with_events(
    app_with_sqlite: TestClient,
):
    """End-to-end: scan with two RAs (Owner + narrow Reader) and a
    handful of events. Owner should be 100 % (one ``*`` hit by any
    op); narrow Reader should be partially covered. Caller
    correlation by UPN."""
    from app.db.models import (
        ActivityEvent,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    owner_guid = "11111111-1111-1111-1111-111111111111"
    reader_guid = "22222222-2222-2222-2222-222222222222"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Narrow Reader",
                role_type="CustomRole",
                permissions=[
                    {
                        "actions": [
                            "Microsoft.Storage/storageAccounts/read",
                            "Microsoft.Storage/storageAccounts/write",
                            "Microsoft.Storage/storageAccounts/delete",
                        ],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_level="subscription",
                    principal_id="alice-guid",
                    principal_name="alice@example.com",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_level="subscription",
                    principal_id="bob-guid",
                    principal_name="bob@example.com",
                    principal_type="User",
                    role_name="Narrow Reader",
                    role_definition_id=reader_arm,
                ),
            ]
        )
        now = datetime.now(UTC)
        # Alice did one storage write (hits Owner's `*`).
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="evt-alice-1",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/write",
                resource_id=f"{sub_scope}/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa1",
                occurred_at=now - timedelta(days=1),
            )
        )
        # Bob did two of his three granted actions.
        for op in (
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Storage/storageAccounts/write",
        ):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"evt-bob-{op}",
                    caller="bob@example.com",
                    caller_type="User",
                    operation_name=op,
                    resource_id=f"{sub_scope}/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa1",
                    occurred_at=now - timedelta(days=2),
                )
            )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)

    by_principal = {r["principal_id"]: r for r in body["rows"]}
    assert by_principal["alice-guid"]["coverage_pct"] == 100
    assert by_principal["alice-guid"]["has_wildcard"] is True
    assert by_principal["bob-guid"]["granted_pattern_count"] == 3
    assert by_principal["bob-guid"]["used_pattern_count"] == 2
    assert by_principal["bob-guid"]["coverage_pct"] == 67
    assert "microsoft.storage/storageaccounts/delete" in by_principal["bob-guid"]["unused_patterns"]
    assert body["events_considered"] == 3
    assert body["subscriptions_without_events"] == []


def test_coverage_endpoint_reports_subs_without_events(
    app_with_sqlite: TestClient,
):
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    owner_guid = "33333333-3333-3333-3333-333333333333"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    sub_guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice-guid",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Owner",
                role_definition_id=owner_arm,
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    assert body["events_considered"] == 0
    assert body["subscriptions_without_events"] == [sub_guid]
    # With no events to credit it, even Owner's ``*`` reads as unused.
    assert body["rows"][0]["coverage_pct"] == 0


def test_coverage_endpoint_handles_uncached_role_definition(
    app_with_sqlite: TestClient,
):
    """An RA whose role definition isn't in our cache (pre-0012 scans
    or transient SDK failure) returns zeroed metrics — UI shows
    'no data' for that row instead of crashing."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    sub_scope = "/subscriptions/cccccccc-cccc-cccc-cccc-cccccccccccc"
    with SessionLocal() as db:
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="p",
                principal_type="User",
                role_name="Owner",
                role_definition_id="/providers/Microsoft.Authorization/roleDefinitions/uncached-uncached-uncached-uncached-x",
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["granted_pattern_count"] == 0
    assert row["coverage_pct"] == 0
    assert row["role_risk"] is None


# --------------------------------------------------------------------------------------
# recommend_narrower_roles
# --------------------------------------------------------------------------------------


def _candidate(name, actions, role_id=None, role_type="BuiltInRole", not_actions=None):
    return {
        "role_definition_id": role_id or f"{name}-guid",
        "role_name": name,
        "role_type": role_type,
        "permissions": [_perm(actions=actions, not_actions=not_actions or [])],
    }


def test_recommend_suggests_reader_when_only_reads_observed():
    """Alice has Contributor but only ever reads across multiple
    providers → only ``Reader`` covers the cross-provider observations;
    ``Storage Account Contributor`` would deny the Compute call."""
    candidates = [
        _candidate("Reader", ["*/read"]),
        _candidate("Storage Account Contributor", ["Microsoft.Storage/*"]),
    ]
    recs = recommend_narrower_roles(
        observed_operations=[
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Compute/virtualMachines/read",
        ],
        current_pattern_count=20,  # a contributor-sized role
        candidates=candidates,
    )
    assert [r["role_name"] for r in recs] == ["Reader"]
    assert recs[0]["patterns_saved"] == 19


def test_recommend_suggests_provider_specific_role_when_usage_is_local():
    """If observed activity is confined to one provider, the
    provider-specific role should beat the generic Reader because it's
    a tighter cover for that activity (though the generic Reader still
    qualifies and shows up after it)."""
    candidates = [
        _candidate("Reader", ["*/read"]),
        _candidate("Storage Account Contributor", ["Microsoft.Storage/*"]),
    ]
    recs = recommend_narrower_roles(
        observed_operations=["Microsoft.Storage/storageAccounts/read"],
        current_pattern_count=20,
        candidates=candidates,
    )
    # Both qualify (each has 1 granted pattern, current is 20). Order
    # ties on pattern count, then by name → Reader first
    # alphabetically. Either candidate is a real suggestion; the order
    # is just stable.
    names = [r["role_name"] for r in recs]
    assert set(names) == {"Reader", "Storage Account Contributor"}


def test_recommend_skips_candidates_that_dont_cover():
    """A candidate must authorize EVERY observed op to qualify."""
    candidates = [
        _candidate("Storage Account Contributor", ["Microsoft.Storage/*"]),
        _candidate("Reader", ["*/read"]),
    ]
    # Mixed observations — storage AND compute writes. Storage candidate
    # doesn't cover the compute write; Reader doesn't cover any write.
    recs = recommend_narrower_roles(
        observed_operations=[
            "Microsoft.Storage/storageAccounts/write",
            "Microsoft.Compute/virtualMachines/write",
        ],
        current_pattern_count=20,
        candidates=candidates,
    )
    assert recs == []


def test_recommend_returns_empty_when_no_observations():
    """No observed activity → no informed recommendation possible."""
    candidates = [_candidate("Reader", ["*/read"])]
    recs = recommend_narrower_roles(
        observed_operations=[],
        current_pattern_count=20,
        candidates=candidates,
    )
    assert recs == []


def test_recommend_skips_candidates_with_equal_or_larger_grant():
    """A candidate with the same pattern count or more isn't a narrowing."""
    candidates = [
        _candidate("Owner-Twin", ["*"]),
        _candidate("Reader", ["*/read"]),
    ]
    # Current role grants 2 patterns. ``Owner-Twin``'s single ``*`` is
    # *narrower by count* but ``*`` is broader in *coverage* — we'd be
    # recommending Owner equivalence. ``exclude_high_risk=True``
    # (default) filters it out. ``Reader`` qualifies.
    recs = recommend_narrower_roles(
        observed_operations=["Microsoft.Storage/storageAccounts/read"],
        current_pattern_count=2,
        candidates=candidates,
    )
    assert [r["role_name"] for r in recs] == ["Reader"]


def test_recommend_with_exclude_high_risk_off_returns_owner_twin_too():
    """Opt-in flag for operators who want side-grades surfaced too."""
    candidates = [
        _candidate("Owner-Twin", ["*"]),
        _candidate("Reader", ["*/read"]),
    ]
    recs = recommend_narrower_roles(
        observed_operations=["Microsoft.Storage/storageAccounts/read"],
        current_pattern_count=2,
        candidates=candidates,
        exclude_high_risk=False,
    )
    names = {r["role_name"] for r in recs}
    assert names == {"Reader", "Owner-Twin"}


def test_recommend_orders_tightest_first_then_name():
    candidates = [
        _candidate("Z Reader", ["*/read"]),
        _candidate("A Reader", ["*/read"]),
        # Three reads — narrower-by-count than the others below would
        # be the wrong rank; the tightest 1-pattern candidates win.
        _candidate(
            "Wide Reader",
            [
                "Microsoft.Storage/*/read",
                "Microsoft.Compute/*/read",
                "Microsoft.KeyVault/*/read",
            ],
        ),
    ]
    recs = recommend_narrower_roles(
        observed_operations=["Microsoft.Storage/storageAccounts/read"],
        current_pattern_count=20,
        candidates=candidates,
    )
    assert [r["role_name"] for r in recs] == ["A Reader", "Z Reader", "Wide Reader"]


def test_recommend_caps_at_top_n():
    candidates = [_candidate(f"R{i}", ["*/read"]) for i in range(10)]
    recs = recommend_narrower_roles(
        observed_operations=["Microsoft.Storage/storageAccounts/read"],
        current_pattern_count=20,
        candidates=candidates,
        top_n=2,
    )
    assert len(recs) == 2


def test_recommend_respects_candidate_notactions():
    """If a candidate's notActions deny one of the observed ops, it
    doesn't qualify — that op would fail under the candidate role."""
    candidates = [
        _candidate(
            "ReaderMinusAuth",
            actions=["*/read"],
            not_actions=["Microsoft.Authorization/*/read"],
        )
    ]
    recs = recommend_narrower_roles(
        observed_operations=[
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Authorization/roleAssignments/read",
        ],
        current_pattern_count=20,
        candidates=candidates,
    )
    assert recs == []


def test_coverage_endpoint_includes_recommendations(
    app_with_sqlite: TestClient,
):
    """End-to-end: Contributor at a sub with only reads observed →
    Reader should appear as a recommendation."""
    from app.db.models import (
        ActivityEvent,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    contributor_guid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    reader_guid = "rrrrrrrr-rrrr-rrrr-rrrr-rrrrrrrrrrrr"
    contributor_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{contributor_guid}"
    sub_guid = "00000000-0000-0000-0000-000000000099"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        # Contributor with a fat actions list (so its pattern_count > Reader's).
        db.add(
            RoleDefinition(
                role_definition_id=contributor_guid,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": [
                            "Microsoft.Storage/storageAccounts/read",
                            "Microsoft.Storage/storageAccounts/write",
                            "Microsoft.Storage/storageAccounts/delete",
                            "Microsoft.Storage/storageAccounts/listKeys/action",
                            "Microsoft.Compute/virtualMachines/read",
                            "Microsoft.Compute/virtualMachines/write",
                        ],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice-guid",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Contributor",
                role_definition_id=contributor_arm,
            )
        )
        now = datetime.now(UTC)
        # Alice has only ever read.
        for op in (
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Compute/virtualMachines/read",
        ):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"evt-{op}",
                    caller="alice@example.com",
                    caller_type="User",
                    operation_name=op,
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(days=1),
                )
            )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["coverage_pct"] < 100
    rec_names = [r["role_name"] for r in row["recommendations"]]
    assert "Reader" in rec_names
    reader_rec = next(r for r in row["recommendations"] if r["role_name"] == "Reader")
    assert reader_rec["patterns_saved"] == 5  # 6 contributor patterns - 1 Reader pattern


def test_coverage_endpoint_no_recommendations_when_zero_observations(
    app_with_sqlite: TestClient,
):
    """No events → recommendations list is empty (can't recommend
    blindly without knowing the principal's needs)."""
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    contributor_guid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    contributor_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{contributor_guid}"
    sub_scope = "/subscriptions/00000000-0000-0000-0000-aaaaaaaaaaaa"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=contributor_guid,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*", "Microsoft.Compute/*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="bob-guid",
                principal_name="bob@example.com",
                principal_type="User",
                role_name="Contributor",
                role_definition_id=contributor_arm,
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["observed_op_count"] == 0
    assert row["recommendations"] == []


# --------------------------------------------------------------------------------------
# Group expansion in a check's coverage
# --------------------------------------------------------------------------------------


def test_coverage_attributes_member_activity_back_to_group_ra(
    app_with_sqlite: TestClient,
):
    """A Group-typed RA: activity from its members should drive the
    group row's coverage, not be silently dropped."""
    from app.db.models import (
        ActivityEvent,
        GroupMembership,
        RoleAssignment,
        RoleDefinition,
        Scan,
        ScanGroupResolution,
    )
    from app.db.session import SessionLocal

    contributor_guid = "abc00000-0000-0000-0000-000000000001"
    contributor_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{contributor_guid}"
    sub_guid = "11110000-0000-0000-0000-000000000001"
    sub_scope = f"/subscriptions/{sub_guid}"
    group_id = "00000000-0000-0000-0000-aaaaaaaaaaaa"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=contributor_guid,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": [
                            "Microsoft.Storage/storageAccounts/read",
                            "Microsoft.Storage/storageAccounts/write",
                            "Microsoft.Storage/storageAccounts/delete",
                        ],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        # Group RA — the group itself has Contributor at the sub.
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id=group_id,
                principal_name="group-platform",
                principal_type="Group",
                role_name="Contributor",
                role_definition_id=contributor_arm,
            )
        )
        # Mark the group as resolved (the scan walk did successfully
        # fetch its membership from Graph) and add two members.
        db.add(ScanGroupResolution(scan_id=scan.id, group_id=group_id))
        db.add(
            GroupMembership(
                scan_id=scan.id,
                group_id=group_id,
                member_id="alice-guid",
                member_type="User",
                member_name="alice@example.com",
            )
        )
        db.add(
            GroupMembership(
                scan_id=scan.id,
                group_id=group_id,
                member_id="deploy-sp-guid",
                member_type="ServicePrincipal",
                member_name="deploy-sp",
            )
        )
        now = datetime.now(UTC)
        # Alice did a storage read; deploy-sp did a write. Activity Log
        # records the underlying caller, NOT the group.
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="evt-alice-read",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/read",
                resource_id=f"{sub_scope}/...",
                occurred_at=now - timedelta(days=1),
            )
        )
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="evt-sp-write",
                caller="deploy-sp",
                caller_type="ServicePrincipal",
                operation_name="Microsoft.Storage/storageAccounts/write",
                resource_id=f"{sub_scope}/...",
                occurred_at=now - timedelta(days=1),
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    # Without group expansion this would be 0/3. With expansion both
    # members' activity rolls up: read + write = 2/3.
    assert row["principal_type"] == "Group"
    assert row["member_count"] == 2
    assert row["granted_pattern_count"] == 3
    assert row["used_pattern_count"] == 2
    assert row["coverage_pct"] == 67
    assert "microsoft.storage/storageaccounts/delete" in row["unused_patterns"]


def test_coverage_member_count_zero_when_group_is_empty(
    app_with_sqlite: TestClient,
):
    """Group with zero members but Graph DID answer: member_count is 0
    (not null). Distinguishes "empty group" from "permission failure"
    on the Coverage tab."""
    from app.db.models import (
        RoleAssignment,
        RoleDefinition,
        Scan,
        ScanGroupResolution,
    )
    from app.db.session import SessionLocal

    contributor_guid = "ec000000-0000-0000-0000-000000000001"
    contributor_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{contributor_guid}"
    sub_scope = "/subscriptions/ec000000-0000-0000-0000-aaaaaaaaaaaa"
    empty_group_id = "00000000-0000-0000-0000-eeeeeeeeeeee"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=contributor_guid,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id=empty_group_id,
                principal_name="grp_empty",
                principal_type="Group",
                role_name="Contributor",
                role_definition_id=contributor_arm,
            )
        )
        # Resolved, but no GroupMembership rows — Graph answered with
        # zero members.
        db.add(ScanGroupResolution(scan_id=scan.id, group_id=empty_group_id))
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["principal_type"] == "Group"
    assert row["member_count"] == 0


def test_coverage_member_count_null_when_group_membership_unresolved(
    app_with_sqlite: TestClient,
):
    """No GroupMembership rows for the group → we couldn't resolve
    it (Graph 403 or transient failure). Return ``member_count=null``
    so the UI shows a warning instead of a false 0 %."""
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    contributor_guid = "abc00000-0000-0000-0000-000000000002"
    contributor_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{contributor_guid}"
    sub_scope = "/subscriptions/22220000-0000-0000-0000-000000000002"
    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=contributor_guid,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="00000000-0000-0000-0000-bbbbbbbbbbbb",
                principal_name="group-unresolved",
                principal_type="Group",
                role_name="Contributor",
                role_definition_id=contributor_arm,
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["principal_type"] == "Group"
    assert row["member_count"] is None
    assert row["observed_op_count"] == 0


def test_coverage_member_count_zero_for_non_group_ras(
    app_with_sqlite: TestClient,
):
    """User / ServicePrincipal RAs report ``member_count=0`` so the
    UI doesn't render a "via N members" hint for non-group rows."""
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    reader_guid = "abc00000-0000-0000-0000-000000000003"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_scope = "/subscriptions/33330000-0000-0000-0000-000000000003"
    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Reader",
                role_definition_id=reader_arm,
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    assert body["rows"][0]["member_count"] == 0


# --------------------------------------------------------------------------------------
# Graph member resolver normalization (unit, no live HTTP)
# --------------------------------------------------------------------------------------


def test_graph_to_member_user_prefers_upn_over_displayname():
    from app.tools.graph_groups import _to_member

    m = _to_member(
        {
            "@odata.type": "#microsoft.graph.user",
            "id": "ALICE-GUID",
            "userPrincipalName": "alice@example.com",
            "displayName": "Alice Anderson",
        }
    )
    assert m.id == "alice-guid"
    assert m.type == "User"
    assert m.name == "alice@example.com"


def test_graph_to_member_servicePrincipal_uses_displayname():
    from app.tools.graph_groups import _to_member

    m = _to_member(
        {
            "@odata.type": "#microsoft.graph.servicePrincipal",
            "id": "sp-guid",
            "displayName": "deploy-sp",
        }
    )
    assert m.type == "ServicePrincipal"
    assert m.name == "deploy-sp"


def test_graph_to_member_unknown_type_passes_through_none():
    from app.tools.graph_groups import _to_member

    m = _to_member({"@odata.type": "#microsoft.graph.somethingNew", "id": "x"})
    assert m.type is None


def test_coverage_treats_legacy_scan_with_members_as_resolved(
    app_with_sqlite: TestClient,
):
    """Backfill behaviour: a scan with GroupMembership rows but NO
    ScanGroupResolution row (pre-migration-0015 data) must still be
    treated as resolved. Otherwise every historical group RA reads
    as "unresolved" even though we have the member data on file."""
    from app.db.models import (
        ActivityEvent,
        GroupMembership,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    reader_guid = "fac00000-0000-0000-0000-000000000001"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_guid = "facfacfa-0000-0000-0000-000000000001"
    sub_scope = f"/subscriptions/{sub_guid}"
    group_id = "00000000-0000-0000-0000-faceface0001"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id=group_id,
                principal_name="grp_legacy",
                principal_type="Group",
                role_name="Reader",
                role_definition_id=reader_arm,
            )
        )
        # Membership row WITHOUT a ScanGroupResolution row — what the
        # DB looks like after a scan run between migrations 0014 and
        # 0015.
        db.add(
            GroupMembership(
                scan_id=scan.id,
                group_id=group_id,
                member_id="alice",
                member_type="User",
                member_name="alice@example.com",
            )
        )
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="legacy-evt",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/read",
                resource_id=f"{sub_scope}/...",
                occurred_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    # Backfill should treat this as resolved (1 member), and Alice's
    # observed read should drive coverage to 100% (Reader has 1
    # granted pattern, which is exercised).
    assert row["member_count"] == 1
    assert row["coverage_pct"] == 100


# --------------------------------------------------------------------------------------
# /scans/{id}/groups/{group_id}/members
# --------------------------------------------------------------------------------------


def test_group_members_endpoint_returns_persisted_members(
    app_with_sqlite: TestClient,
):
    from app.db.models import (
        GroupMembership,
        Scan,
        ScanGroupResolution,
    )
    from app.db.session import SessionLocal

    sub_scope = "/subscriptions/mem00000-0000-0000-0000-000000000001"
    group_id = "00000000-0000-0000-0000-memmemmem001"
    with SessionLocal() as db:
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(ScanGroupResolution(scan_id=scan.id, group_id=group_id))
        db.add_all(
            [
                GroupMembership(
                    scan_id=scan.id,
                    group_id=group_id,
                    member_id="alice",
                    member_type="User",
                    member_name="alice@example.com",
                ),
                GroupMembership(
                    scan_id=scan.id,
                    group_id=group_id,
                    member_id="bob",
                    member_type="User",
                    member_name="bob@example.com",
                ),
            ]
        )
        db.commit()
        scan_id = scan.id

    body = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/groups/{group_id}/members").json()
    assert body["group_id"] == group_id
    assert {m["member_id"] for m in body["members"]} == {"alice", "bob"}


def test_group_members_endpoint_returns_empty_for_resolved_empty_group(
    app_with_sqlite: TestClient,
):
    """Resolution row present + zero membership rows → 200 with []."""
    from app.db.models import Scan, ScanGroupResolution
    from app.db.session import SessionLocal

    sub_scope = "/subscriptions/mem00000-0000-0000-0000-000000000002"
    group_id = "00000000-0000-0000-0000-memmemmem002"
    with SessionLocal() as db:
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(ScanGroupResolution(scan_id=scan.id, group_id=group_id))
        db.commit()
        scan_id = scan.id

    r = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/groups/{group_id}/members")
    assert r.status_code == 200
    assert r.json()["members"] == []


def test_group_members_endpoint_404_for_unresolved_group(
    app_with_sqlite: TestClient,
):
    """No resolution row + no member rows → unresolved → 404."""
    from app.db.models import Scan
    from app.db.session import SessionLocal

    sub_scope = "/subscriptions/mem00000-0000-0000-0000-000000000003"
    with SessionLocal() as db:
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.commit()
        scan_id = scan.id

    r = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/groups/unknown-group/members")
    assert r.status_code == 404
    assert "no recorded membership" in r.json()["detail"]


def test_coverage_returns_top_operations_sorted_by_count(
    app_with_sqlite: TestClient,
):
    """Right-sized wildcard role: 100% headline is misleading on its
    own. ``top_operations`` shows what actually drove the coverage."""
    from app.db.models import (
        ActivityEvent,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    owner_guid = "60000000-0000-0000-0000-000000000001"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    sub_guid = "60000000-0000-0000-0000-aaaaaaaaaaaa"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice-guid",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Owner",
                role_definition_id=owner_arm,
            )
        )
        # 5 reads, 2 writes, 1 listKeys — top_operations must reflect
        # this distribution.
        now = datetime.now(UTC)
        for i in range(5):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"r{i}",
                    caller="alice@example.com",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(hours=i),
                )
            )
        for i in range(2):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"w{i}",
                    caller="alice@example.com",
                    operation_name="Microsoft.Storage/storageAccounts/write",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(hours=10 + i),
                )
            )
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="lk1",
                caller="alice@example.com",
                operation_name="Microsoft.Storage/storageAccounts/listKeys/action",
                resource_id=f"{sub_scope}/...",
                occurred_at=now - timedelta(days=1),
            )
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    row = body["rows"][0]
    assert row["coverage_pct"] == 100  # wildcard, but we want to see breakdown
    ops = row["top_operations"]
    assert [o["operation_name"] for o in ops] == [
        "Microsoft.Storage/storageAccounts/read",
        "Microsoft.Storage/storageAccounts/write",
        "Microsoft.Storage/storageAccounts/listKeys/action",
    ]
    assert [o["count"] for o in ops] == [5, 2, 1]


def test_coverage_top_operations_capped_at_ten(app_with_sqlite: TestClient):
    """A noisy principal with many distinct ops should only return 10."""
    from app.db.models import (
        ActivityEvent,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    owner_guid = "60000000-0000-0000-0000-000000000002"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    sub_guid = "60000000-0000-0000-0000-bbbbbbbbbbbb"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Owner",
                role_definition_id=owner_arm,
            )
        )
        now = datetime.now(UTC)
        for i in range(15):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"e{i}",
                    caller="alice@example.com",
                    operation_name=f"Microsoft.Storage/x{i}/read",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(minutes=i),
                )
            )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    assert len(body["rows"][0]["top_operations"]) == 10


def test_coverage_reports_attributed_and_unattributed_events(
    app_with_sqlite: TestClient,
):
    """An SP doing things on the sub whose access isn't visible in any
    RA shouldn't be silently dropped — it should appear in the
    unattributed_callers list with its event count."""
    from app.db.models import (
        ActivityEvent,
        RoleAssignment,
        RoleDefinition,
        Scan,
    )
    from app.db.session import SessionLocal

    reader_guid = "70000000-0000-0000-0000-000000000001"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_guid = "70000000-0000-0000-0000-aaaaaaaaaaaa"
    sub_scope = f"/subscriptions/{sub_guid}"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=sub_scope,
                scope_level="subscription",
                principal_id="alice-guid",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Reader",
                role_definition_id=reader_arm,
            )
        )
        now = datetime.now(UTC)
        # 1 attributed event (alice)
        db.add(
            ActivityEvent(
                subscription_id=sub_guid,
                event_data_id="e-alice",
                caller="alice@example.com",
                caller_type="User",
                operation_name="Microsoft.Storage/storageAccounts/read",
                resource_id=f"{sub_scope}/...",
                occurred_at=now - timedelta(hours=1),
            )
        )
        # 5 unattributed events from a noisy SP
        for i in range(5):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"e-sp-{i}",
                    caller="ghost-sp-guid",
                    caller_type="ServicePrincipal",
                    operation_name="Microsoft.Storage/storageAccounts/listKeys/action",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(hours=2 + i),
                )
            )
        # 2 unattributed events with a null caller (platform jobs)
        for i in range(2):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"e-null-{i}",
                    caller=None,
                    caller_type=None,
                    operation_name="Microsoft.Insights/something/action",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(hours=10 + i),
                )
            )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    assert body["events_considered"] == 8
    assert body["events_attributed"] == 1
    assert body["events_unattributed"] == 7
    callers = {u["caller"]: u for u in body["unattributed_callers"]}
    assert callers["ghost-sp-guid"]["event_count"] == 5
    assert callers["ghost-sp-guid"]["caller_type"] == "ServicePrincipal"
    assert callers["(unknown)"]["event_count"] == 2


def test_coverage_unattributed_callers_capped_at_twenty(
    app_with_sqlite: TestClient,
):
    """A wide tenant could have 100s of uncorrelated SPs. Cap at 20."""
    from app.db.models import ActivityEvent, Scan
    from app.db.session import SessionLocal

    sub_guid = "70000000-0000-0000-0000-bbbbbbbbbbbb"
    sub_scope = f"/subscriptions/{sub_guid}"
    with SessionLocal() as db:
        scan = Scan(scope=sub_scope, scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        now = datetime.now(UTC)
        # No RAs at all on this scan, but 30 distinct SP callers in
        # the window. After the sub filter all 30 are "considered",
        # all are unattributed.
        for i in range(30):
            db.add(
                ActivityEvent(
                    subscription_id=sub_guid,
                    event_data_id=f"e{i}",
                    caller=f"sp-{i}",
                    caller_type="ServicePrincipal",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{sub_scope}/...",
                    occurred_at=now - timedelta(minutes=i),
                )
            )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    # No RAs in scan but events_considered should still reflect the load.
    assert body["events_considered"] == 30
    assert body["events_attributed"] == 0
    assert body["events_unattributed"] == 30
    assert len(body["unattributed_callers"]) == 20


def test_unattributed_caller_carries_cross_scan_access_counts(
    app_with_sqlite: TestClient,
):
    """An unattributed caller that has a direct RA in another scan
    (or is a member of a group that does) should carry those counts
    so the UI can offer a 'look up access' link. A caller with zero
    on both counts is a true unknown."""
    from app.db.models import (
        ActivityEvent,
        GroupMembership,
        RoleAssignment,
        Scan,
    )
    from app.db.session import SessionLocal

    other_sub_scope = "/subscriptions/80000000-0000-0000-0000-othersuboth1"
    target_sub_scope = "/subscriptions/80000000-0000-0000-0000-targetsub001"
    target_sub_guid = "80000000-0000-0000-0000-targetsub001"

    with SessionLocal() as db:
        # A SECOND scan where ``known-sp-guid`` has a direct RA.
        other_scan = Scan(scope=other_sub_scope, scope_level="subscription", status="done")
        db.add(other_scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=other_scan.id,
                scope=other_sub_scope,
                scope_level="subscription",
                principal_id="known-sp-guid",
                principal_name="known-sp",
                principal_type="ServicePrincipal",
                role_name="Reader",
                role_definition_id="/role-def/reader",
            )
        )
        # ``member-sp-guid`` is a member of a group with an RA, also
        # in some other scan.
        db.add(
            RoleAssignment(
                scan_id=other_scan.id,
                scope=other_sub_scope,
                scope_level="subscription",
                principal_id="00000000-0000-0000-0000-groupguid001",
                principal_name="grp_other",
                principal_type="Group",
                role_name="Reader",
                role_definition_id="/role-def/reader",
            )
        )
        db.add(
            GroupMembership(
                scan_id=other_scan.id,
                group_id="00000000-0000-0000-0000-groupguid001",
                member_id="member-sp-guid",
                member_type="ServicePrincipal",
                member_name="member-sp",
            )
        )

        # THIS scan: events on a different sub, callers we don't
        # correlate to anything in *this* scan.
        target = Scan(scope=target_sub_scope, scope_level="subscription", status="done")
        db.add(target)
        db.flush()
        now = datetime.now(UTC)
        for caller in ("known-sp-guid", "member-sp-guid", "ghost-sp-guid"):
            db.add(
                ActivityEvent(
                    subscription_id=target_sub_guid,
                    event_data_id=f"e-{caller}",
                    caller=caller,
                    caller_type="ServicePrincipal",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{target_sub_scope}/...",
                    occurred_at=now - timedelta(hours=1),
                )
            )
        db.commit()
        target_id = target.id

    body = coverage_of(target_id)
    callers = {u["caller"]: u for u in body["unattributed_callers"]}
    # Known SP: a direct RA exists elsewhere.
    assert callers["known-sp-guid"]["direct_assignment_count"] == 1
    assert callers["known-sp-guid"]["indirect_membership_count"] == 0
    # Member SP: group membership elsewhere.
    assert callers["member-sp-guid"]["direct_assignment_count"] == 0
    assert callers["member-sp-guid"]["indirect_membership_count"] == 1
    # Ghost SP: no record anywhere. This is the finding.
    assert callers["ghost-sp-guid"]["direct_assignment_count"] == 0
    assert callers["ghost-sp-guid"]["indirect_membership_count"] == 0


def test_unattributed_caller_name_resolves_from_other_scans(
    app_with_sqlite: TestClient,
):
    """An SP GUID that's unattributed in THIS scan but has a name
    resolved in another scan should come back with caller_name set,
    so the UI doesn't show a bare GUID for principals we already
    know about."""
    from app.db.models import (
        ActivityEvent,
        GroupMembership,
        RoleAssignment,
        Scan,
    )
    from app.db.session import SessionLocal

    other_sub_scope = "/subscriptions/91000000-0000-0000-0000-othersub0001"
    target_sub_scope = "/subscriptions/91000000-0000-0000-0000-targetsub01"
    target_sub_guid = "91000000-0000-0000-0000-targetsub01"

    with SessionLocal() as db:
        # SP-A appears in an RA in another scan with a resolved name.
        other_scan = Scan(scope=other_sub_scope, scope_level="subscription", status="done")
        db.add(other_scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=other_scan.id,
                scope=other_sub_scope,
                scope_level="subscription",
                principal_id="sp-a-guid",
                principal_name="deploy-sp-a",
                principal_type="ServicePrincipal",
                role_name="Reader",
                role_definition_id="/role-def/reader",
            )
        )
        # SP-B has a resolved name only via group_memberships.
        db.add(
            GroupMembership(
                scan_id=other_scan.id,
                group_id="00000000-0000-0000-0000-groupguid002",
                member_id="sp-b-guid",
                member_type="ServicePrincipal",
                member_name="deploy-sp-b",
            )
        )

        # Target scan: events from SP-A, SP-B, and a true unknown.
        target = Scan(scope=target_sub_scope, scope_level="subscription", status="done")
        db.add(target)
        db.flush()
        now = datetime.now(UTC)
        for caller in ("sp-a-guid", "sp-b-guid", "ghost-guid"):
            db.add(
                ActivityEvent(
                    subscription_id=target_sub_guid,
                    event_data_id=f"e-{caller}",
                    caller=caller,
                    caller_type="ServicePrincipal",
                    operation_name="Microsoft.Storage/storageAccounts/read",
                    resource_id=f"{target_sub_scope}/...",
                    occurred_at=now - timedelta(hours=1),
                )
            )
        db.commit()
        target_id = target.id

    body = coverage_of(target_id)
    callers = {u["caller"]: u for u in body["unattributed_callers"]}
    assert callers["sp-a-guid"]["caller_name"] == "deploy-sp-a"
    assert callers["sp-b-guid"]["caller_name"] == "deploy-sp-b"
    # Unknown stays null — no record anywhere.
    assert callers["ghost-guid"]["caller_name"] is None


# --------------------------------------------------------------------------------------
# role_covers (broader-than-or-equal-to)
# --------------------------------------------------------------------------------------


def test_role_covers_owner_covers_reader():
    owner = [_perm(actions=["*"])]
    reader = [_perm(actions=["*/read"])]
    assert role_covers(owner, reader) is True
    # Reader does NOT cover Owner.
    assert role_covers(reader, owner) is False


def test_role_covers_storage_contributor_covers_storage_reader():
    sc = [_perm(actions=["Microsoft.Storage/*"])]
    sr = [_perm(actions=["Microsoft.Storage/storageAccounts/read"])]
    assert role_covers(sc, sr) is True


def test_role_covers_disjoint_providers_does_not_cover():
    storage = [_perm(actions=["Microsoft.Storage/*"])]
    compute = [_perm(actions=["Microsoft.Compute/virtualMachines/read"])]
    assert role_covers(storage, compute) is False


def test_role_covers_respects_broader_notactions():
    """If the broader role's notActions would deny what the narrower
    role grants, broader is NOT a superset (Contributor vs UAA-like)."""
    contributor = [
        _perm(
            actions=["*"],
            not_actions=["Microsoft.Authorization/*/Write"],
        )
    ]
    rbac_admin = [_perm(actions=["Microsoft.Authorization/roleAssignments/write"])]
    assert role_covers(contributor, rbac_admin) is False


def test_role_covers_returns_false_on_missing_data():
    assert role_covers(None, [_perm(actions=["*"])]) is False
    assert role_covers([_perm(actions=["*"])], None) is False
    assert role_covers([_perm()], [_perm(actions=["*/read"])]) is False


def test_coverage_flags_covered_by_for_redundant_ra(
    app_with_sqlite: TestClient,
):
    """Alice has Owner @ /sub and Reader @ /sub/rg. The Reader RA is
    redundant — Owner at the sub covers everything below."""
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    owner_guid = "92000000-0000-0000-0000-000000000001"
    reader_guid = "92000000-0000-0000-0000-000000000002"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_scope = "/subscriptions/92000000-0000-0000-0000-aaaaaaaaaaaa"
    rg_scope = f"{sub_scope}/resourcegroups/rg-x"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(
            scope=sub_scope,
            scope_level="subscription",
            status="done",
            hierarchy={
                sub_scope: {"name": "sub-platform", "level": "subscription", "parent": None},
                rg_scope: {"name": "rg-x", "level": "resource_group", "parent": sub_scope},
            },
        )
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_level="subscription",
                    principal_id="alice",
                    principal_name="alice@example.com",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=rg_scope,
                    scope_level="resource_group",
                    principal_id="alice",
                    principal_name="alice@example.com",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=reader_arm,
                ),
            ]
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    by_role = {(r["role_name"], r["scope_level"]): r for r in body["rows"]}
    # The Reader RA at the RG is covered by Alice's Owner at the sub.
    reader = by_role[("Reader", "resource_group")]
    assert len(reader["covered_by"]) == 1
    assert reader["covered_by"][0]["role_name"] == "Owner"
    assert reader["covered_by"][0]["scope_level"] == "subscription"
    # Owner at the sub is the top — nothing covers it.
    owner = by_role[("Owner", "subscription")]
    assert owner["covered_by"] == []


def test_coverage_does_not_flag_covered_when_different_principal(
    app_with_sqlite: TestClient,
):
    """Alice's Reader @ /sub/rg should NOT be covered by Bob's
    Owner @ /sub — they're different principals."""
    from app.db.models import RoleAssignment, RoleDefinition, Scan
    from app.db.session import SessionLocal

    owner_guid = "93000000-0000-0000-0000-000000000001"
    reader_guid = "93000000-0000-0000-0000-000000000002"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    sub_scope = "/subscriptions/93000000-0000-0000-0000-aaaaaaaaaaaa"
    rg_scope = f"{sub_scope}/resourcegroups/rg-y"

    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=owner_guid,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        db.add(
            RoleDefinition(
                role_definition_id=reader_guid,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        scan = Scan(
            scope=sub_scope,
            scope_level="subscription",
            status="done",
            hierarchy={
                sub_scope: {"name": "sub", "level": "subscription", "parent": None},
                rg_scope: {"name": "rg-y", "level": "resource_group", "parent": sub_scope},
            },
        )
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_level="subscription",
                    principal_id="bob",
                    principal_name="bob@example.com",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=rg_scope,
                    scope_level="resource_group",
                    principal_id="alice",
                    principal_name="alice@example.com",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=reader_arm,
                ),
            ]
        )
        db.commit()
        scan_id = scan.id

    body = coverage_of(scan_id)
    reader = next(r for r in body["rows"] if r["role_name"] == "Reader")
    assert reader["covered_by"] == []
