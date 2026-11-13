"""The playbook (core/advice.py): one test per rule, then how rules combine.

Rules are tested against a small fake of ``CurrentView``; the API wiring is
covered at the end against the seeded SQLite world of test_registry.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import advice
from app.core.advice_texts import create_command, delete_command
from app.schemas.advice import AdviceSettings

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
MG = "/providers/microsoft.management/managementgroups/mg-prod"
SUB = "/subscriptions/11111111-1111-1111-1111-111111111111"
RG = f"{SUB}/resourcegroups/rg-app"
OWNER = "/providers/Microsoft.Authorization/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
READER = "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"


class FakeView:
    def __init__(
        self,
        ras: list[Any],
        levels: dict[int, str],
        findings: dict[int, list[Any]] | None = None,
        groups: dict[str, set[str]] | None = None,
    ) -> None:
        self.ras = ras
        self._levels = levels
        self.findings_by_ra = findings or {}
        self._groups = groups or {}
        self.name = {MG: "mg-prod", SUB: "prod", RG: "rg-app"}
        self.parent = {SUB: MG, RG: SUB, MG: None}
        self.owner = {ra.id: ra.scope.lower() for ra in ras}

    def capability_of(self, ra: Any) -> Any:
        return SimpleNamespace(level=self._levels.get(ra.id, "view"))

    def ancestors(self, scope: str) -> list[str]:
        out, cur = [], self.parent.get(scope)
        while cur:
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def groups_of(self, ids: set[str]) -> dict[str, set[str]]:
        return {i: self._groups[i] for i in ids if i in self._groups}


_next = iter(range(1, 10_000))


def ra(
    scope: str = SUB,
    level_name: str = "subscription",
    *,
    pid: str = "alice-id",
    name: str | None = "alice@example.com",
    ptype: str = "User",
    role: str = "Owner",
    role_id: str = OWNER,
    **kw: Any,
) -> Any:
    return SimpleNamespace(
        id=next(_next),
        scope=scope,
        scope_name=None,
        scope_level=level_name,
        principal_id=pid,
        principal_name=name,
        principal_type=ptype,
        role_name=role,
        role_definition_id=role_id,
        assignment_type=kw.get("assignment_type", "Assigned"),
        end_at=kw.get("end_at"),
        created_on=kw.get("created_on"),
        created_by=kw.get("created_by", "admin-id"),
        created_by_name=kw.get("created_by_name", "Admin"),
        condition=kw.get("condition"),
        principal_app_owner=kw.get("app_owner"),
    )


def finding(category: str, status: str = "open", **evidence: Any) -> Any:
    return SimpleNamespace(
        status=status, finding=SimpleNamespace(category=category, evidence=evidence)
    )


def ctx_for(
    view: FakeView, *, responsible: set[str] | None = None, **settings: Any
) -> advice.AdviceContext:
    covered = {MG, SUB, RG} if responsible is None else responsible
    return advice.AdviceContext(
        view=view,
        settings=AdviceSettings(**settings),
        now=NOW,
        responsible={s: SimpleNamespace() for s in covered},  # type: ignore[misc]
    )


def advise(grant: Any, level: str = "access", findings: list[Any] | None = None, **kw: Any):
    view = FakeView(
        [grant, *kw.pop("others", [])],
        {grant.id: level},
        {grant.id: findings or []},
        kw.pop("groups", None),
    )
    return advice.advise_grant(ctx_for(view, **kw), grant)


# ---- one test per rule ---------------------------------------------------------------------


def test_r01_orphan_is_removed():
    a = advise(ra(name=None), "view", [finding("unresolved_principal")])
    assert (a.rule, a.weight, a.decision) == ("R01 orphan", "must", "remove")
    assert a.commands == [delete_command("alice-id", "Owner", SUB)]
    assert a.controls == ["5.16", "5.18"]


def test_r02_standing_admin_high_up_goes_to_pim():
    a = advise(ra())
    assert (a.rule, a.action, a.decision) == ("R02 standing-admin", "pim", "change")
    assert "Eligible" in a.how[0] and "prod" in a.how[0]


def test_r02_not_for_pim_activated_time_bound_or_low_places():
    assert advise(ra(assignment_type="Activated")).rule == "R00 fine"
    assert advise(ra(end_at=NOW + timedelta(days=60))).rule == "R00 fine"
    assert advise(ra(RG, "resource_group")).rule == "R00 fine"


def test_r03_idle_admin_is_removed_instead_of_pim():
    a = advise(ra(), findings=[finding("unused", window_days=90)])
    assert (a.rule, a.decision) == ("R03 idle-admin", "remove")
    assert "90 days" in a.why and "reads are not recorded" in a.why
    assert "R02 standing-admin" not in [n.rule for n in a.also]
    # An app that made no change may still read every day: no "remove", the app rule applies.
    app = advise(
        ra(pid="sp-9", ptype="ServicePrincipal"), findings=[finding("unused", window_days=90)]
    )
    assert app.rule == "R09 app-owner"


def test_break_glass_is_exempt_from_the_admin_rules():
    assert advise(ra(pid="bg-id"), break_glass=["bg-id"]).rule == "R00 fine"
    assert (
        advise(ra(pid="bg-id"), findings=[finding("unused")], break_glass=["bg-id"]).rule
        == "R00 fine"
    )


def test_r04_guest_with_power_is_asked_about():
    a = advise(
        ra(RG, "resource_group", name="ext_bob#EXT#@tenant.onmicrosoft.com", role="Contributor"),
        "change",
    )
    assert (a.rule, a.weight, a.decision) == ("R04 guest-power", "must", "ask")
    assert (
        advise(ra(RG, "resource_group", name="bob#EXT#@t", role="Reader"), "view").rule
        == "R00 fine"
    )


def test_r05_keys_high_up_and_standing():
    a = advise(ra(role="Key Vault Secrets Officer"), "keys")
    assert (a.rule, a.weight, a.action) == ("R05 keys-broad", "should", "pim")
    assert a.how[0].startswith("Give it only on the key vaults")
    # Apps can't use PIM: no advice from this rule (Azure's own identities often hold such roles).
    assert (
        advise(ra(pid="sp-2", ptype="ServicePrincipal", role="Contributor"), "keys").rule
        == "R00 fine"
    )


def test_r06_over_broad_names_the_smaller_role_and_both_commands():
    f = finding("over_broad", recommendations=[{"role_name": "Website Contributor"}])
    a = advise(ra(RG, "resource_group", role="Contributor"), "change", [f])
    assert (a.rule, a.decision) == ("R06 over-broad", "change")
    assert a.commands == [
        create_command("alice-id", "User", "Website Contributor", RG),
        delete_command("alice-id", "Contributor", RG),
    ]


def test_r07_redundant_names_the_grant_above():
    f = finding("redundant", covered_by=[{"role_name": "Owner", "scope_name": "mg-prod"}])
    a = advise(ra(RG, "resource_group", role="Reader", role_id=READER), "view", [f])
    assert a.rule == "R07 redundant" and "Owner on mg-prod" in a.why


def test_r08_direct_grant_also_given_through_a_group_above():
    group_grant = ra(
        SUB, pid="grp-1", name="grp-readers", ptype="Group", role="Reader", role_id=READER
    )
    mine = ra(RG, "resource_group", role="Reader", role_id=READER)
    a = advise(mine, "view", others=[group_grant], groups={"alice-id": {"grp-1"}})
    assert (a.rule, a.decision) == ("R08 direct-user", "remove")
    # Not in that group: nothing to do.
    assert (
        advise(
            ra(RG, "resource_group", role="Reader", role_id=READER),
            "view",
            others=[group_grant],
            groups={"alice-id": set()},
        ).rule
        == "R00 fine"
    )


def test_r09_app_that_can_change_access():
    a = advise(ra(RG, "resource_group", pid="sp-1", name="deploy-bot", ptype="ServicePrincipal"))
    assert (a.rule, a.action) == ("R09 app-owner", "replace")
    # Apps never get the PIM advice.
    assert "R02 standing-admin" not in [
        n.rule for n in advise(ra(pid="sp-1", ptype="ServicePrincipal")).also
    ]


def test_r16_microsoft_apps_keep_the_roles_their_service_gave_them():
    ms = "f8cdef31-a31e-4b4a-93e4-5f571e91255a"
    defender = ra(
        pid="sp-def",
        name="CloudPosture/securityOperators/DefenderCSPMSecurityOperator",
        ptype="ServicePrincipal",
        role="Role Based Access Control Administrator",
        app_owner=ms,
    )
    a = advise(defender, "access", [finding("unused"), finding("drift_added")], responsible=set())
    assert (a.rule, a.weight, a.decision) == ("R16 microsoft-app", "info", "keep")
    assert "Defender for Cloud" in a.why and a.commands == []
    assert [n.rule for n in a.also] == ["R13 no-responsible"]  # the place still needs someone
    # The tenant's own app, same facts: the app rule applies.
    own = ra(
        pid="sp-own", ptype="ServicePrincipal", app_owner="11111111-2222-3333-4444-555555555555"
    )
    assert advise(own, "access").rule == "R09 app-owner"


def test_identity_advice_counts_microsoft_apps_as_nothing_to_do():
    ms_grant = ra(
        pid="sp-def", ptype="ServicePrincipal", app_owner="f8cdef31-a31e-4b4a-93e4-5f571e91255a"
    )
    view = FakeView([ms_grant], {ms_grant.id: "access"})
    out = advice.advise_identity(ctx_for(view), "sp-def")
    assert (out.actions, out.fine) == ([], 1)


def test_r10_custom_role_replaceable():
    f = finding("custom_replaceable", recommendations=[{"role_name": "Reader"}])
    a = advise(ra(RG, "resource_group", role="Ops Custom"), "change", [f])
    assert (a.rule, a.weight) == ("R10 custom-replaceable", "could")


def test_r11_unknown_members_has_no_command():
    a = advise(
        ra(RG, "resource_group", pid="grp-9", ptype="Group", role="Reader"),
        "view",
        [finding("membership_unknown")],
    )
    assert (a.rule, a.action, a.commands) == ("R11 unknown-members", "fix_members", [])


def test_r12_new_grant_from_an_unknown_giver_or_drift():
    a = advise(
        ra(
            RG,
            "resource_group",
            role="Reader",
            created_on=NOW - timedelta(days=3),
            created_by=None,
            created_by_name=None,
        ),
        "view",
    )
    assert a.rule == "R12 new-unexplained" and "could not identify" in a.why
    assert (
        advise(ra(RG, "resource_group", role="Reader"), "view", [finding("drift_added")]).rule
        == "R12 new-unexplained"
    )
    old = ra(
        RG, "resource_group", role="Reader", created_on=NOW - timedelta(days=90), created_by=None
    )
    assert advise(old, "view").rule == "R00 fine"


def test_r13_no_responsible_is_a_note_not_the_action():
    a = advise(ra(RG, "resource_group", role="Reader"), "view", responsible=set())
    assert a.rule == "R00 fine"
    assert [n.rule for n in a.also] == ["R13 no-responsible"]


def test_r14_expiring_and_r15_limited_keep():
    a = advise(ra(RG, "resource_group", role="Reader", end_at=NOW + timedelta(days=5)), "view")
    assert (a.rule, a.decision) == ("R14 expiring", "keep")
    b = advise(ra(RG, "resource_group", role="Reader", condition="@Resource[x] == 'y'"), "view")
    assert (b.rule, b.weight) == ("R15 limited", "info")


def test_accepted_or_resolved_findings_no_longer_drive_advice():
    for status in ("accepted_risk", "resolved"):
        assert (
            advise(
                ra(name=None, role="Reader"), "view", [finding("unresolved_principal", status)]
            ).rule
            == "R00 fine"
        )


# ---- combining -----------------------------------------------------------------------------


def test_highest_weight_wins_and_the_rest_are_listed():
    f = [finding("unresolved_principal"), finding("redundant", covered_by=[])]
    a = advise(ra(name=None), "access", f)
    assert a.rule == "R01 orphan"
    assert [n.rule for n in a.also] == ["R02 standing-admin", "R07 redundant"]


def test_a_duplicated_grant_is_removed_rather_than_made_pim_eligible():
    group_grant = ra(MG, "mgmt_group", pid="grp-admins", name="Tenant Admins", ptype="Group")
    mine = ra(SUB)  # Owner, standing, on a subscription below: R02 alone would say PIM
    a = advise(mine, "access", others=[group_grant], groups={"alice-id": {"grp-admins"}})
    assert (a.rule, a.decision) == ("R08 direct-user", "remove")
    assert "R02 standing-admin" in [n.rule for n in a.also]


def test_identity_advice_groups_by_rule_and_ranks_by_weight_then_breadth():
    grants = [
        ra(MG, "mgmt_group"),
        ra(SUB),
        ra(RG, "resource_group", role="Reader", role_id=READER),
        ra(RG, "resource_group", role="Ops Custom"),
    ]
    levels = {
        grants[0].id: "access",
        grants[1].id: "access",
        grants[2].id: "view",
        grants[3].id: "change",
    }
    findings = {
        grants[3].id: [finding("custom_replaceable", recommendations=[{"role_name": "Reader"}])]
    }
    view = FakeView([*grants, ra(SUB, pid="someone-else")], levels, findings)
    out = advice.advise_identity(ctx_for(view), "ALICE-ID")
    assert [a.rule for a in out.actions] == ["R02 standing-admin", "R10 custom-replaceable"]
    assert [g.scope for g in out.actions[0].grants] == [MG, SUB]  # broadest first
    assert "Same for 1 more grant." in out.actions[0].why
    assert all(len(g.commands) == 1 for g in out.actions[0].grants)
    assert (out.fine, out.more) == (1, 0)
    assert advice.advise_identity(ctx_for(view), "alice-id", limit=1).more == 1


# ---- commands and settings -----------------------------------------------------------------


def test_commands_keep_azure_names_as_single_quoted_literals():
    """Names come from Azure and Entra ID; in a command they stay one literal argument."""
    role = 'Odd "role" $(x) `y` it\'s'
    cmd = delete_command("id", role, SUB)
    assert shlex.quote(role) in cmd and shlex.quote(role).startswith("'")
    assert shlex.split(cmd) == [
        "az",
        "role",
        "assignment",
        "delete",
        "--assignee",
        "id",
        "--role",
        role,
        "--scope",
        SUB,
    ]
    assert "--assignee-principal-type ServicePrincipal" in create_command(
        "id", "MSI", "Reader", SUB
    )


def test_comment_lines_never_break():
    from app.core.advice_texts import comment_text

    assert comment_text("Tom\nsecond line\r\u2028end") == "Tom second line  end"


def test_settings_round_trip_and_bad_values(app_with_sqlite):
    assert app_with_sqlite.get("/api/settings/advice").json()["new_days"] == 30
    r = app_with_sqlite.put(
        "/api/settings/advice",
        json={"new_days": 7, "expiring_days": 14, "break_glass": [" BG-ID ", "bg-id", ""]},
    )
    assert r.status_code == 200
    assert r.json()["break_glass"] == ["bg-id"]
    assert r.json()["break_glass_names"] == [{"principal_id": "bg-id", "name": None}]
    assert app_with_sqlite.put("/api/settings/advice", json={"new_days": 0}).status_code == 422


@pytest.mark.parametrize("path", ["grants", "principals"])
def test_pages_carry_advice(app_with_sqlite, path):
    from app.core.current_view import CurrentView, grant_id
    from app.db.session import SessionLocal
    from tests.test_registry import _seed_world

    with SessionLocal() as db:
        scan_id = _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    with SessionLocal() as db:
        sp_grant = next(
            grant_id(r) for r in CurrentView(db).ras if r.principal_id.lower() == "sp-1"
        )

    if path == "grants":
        body = app_with_sqlite.get(f"/api/grants/{sp_grant}").json()
        assert body["advice"]["rule"].startswith("R")
        assert body["advice"]["title"]
    else:
        body = app_with_sqlite.get("/api/principals/sp-1").json()
        assert body["advice"]["actions"], body["advice"]
        row = body["assignments"][0]
        assert row["advice_weight"] in ("must", "should", "could")
