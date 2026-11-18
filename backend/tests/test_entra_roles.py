"""Entra roles: their levels (core/entra_roles.py) and reading them (tools/entra_directory.py).

``fixtures/entra_builtin_roles.json`` holds Microsoft's own definitions of a
few built-in Entra roles (public data), as Graph returns their permissions.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.core import entra_roles as er
from app.core.capabilities import classify
from app.core.role_risk import classify_permissions, risk_reasons
from app.tools import entra_directory as ed

BUILTIN = json.loads((Path(__file__).parent / "fixtures" / "entra_builtin_roles.json").read_text())


def _perms(name: str, condition: str | None = None) -> list[dict]:
    return [{"allowedResourceActions": BUILTIN[name]["actions"], "condition": condition}]


@pytest.mark.parametrize(
    ("role", "level"),
    [
        ("Global Administrator", "access"),
        ("Privileged Role Administrator", "access"),
        ("Groups Administrator", "access"),
        ("User Administrator", "access"),
        ("Application Administrator", "keys"),
        ("Helpdesk Administrator", "keys"),
        ("Security Administrator", "keys"),
        ("Global Reader", "keys"),  # it can read BitLocker recovery keys
        ("Exchange Administrator", "change"),
        ("Directory Readers", "view"),
        ("Reports Reader", "view"),
        ("Message Center Reader", "view"),  # portal permissions only
    ],
)
def test_each_built_in_role_lands_at_the_level_its_permissions_reach(role, level):
    assert er.level_of(_perms(role))[0] == level
    assert classify(_perms(role)).level == level


def test_the_row_says_what_the_role_gives():
    assert er.level_of(_perms("Privileged Role Administrator"))[1] == ("Entra roles",)
    assert "app secrets" in er.level_of(_perms("Application Administrator"))[1]
    assert er.level_of(_perms("Global Reader"))[1] == ("BitLocker keys",)
    assert er.level_of(_perms("Exchange Administrator")) == (
        "change",
        ("Microsoft 365 group members",),
    )
    assert (
        classify(_perms("Application Administrator")).keys_for
        == er.level_of(_perms("Application Administrator"))[1]
    )


def test_permissions_limited_to_owned_objects_do_not_count():
    owned = [
        {
            "allowedResourceActions": ["microsoft.directory/groups/members/update"],
            "condition": "$SubjectIsOwner",
        },
        {"allowedResourceActions": ["microsoft.directory/users/standard/read"], "condition": None},
    ]
    assert er.level_of(owned) == ("view", ())


def test_azure_roles_are_not_taken_for_entra_ones_and_no_permissions_is_unknown():
    azure = [{"actions": ["*"], "notActions": []}]
    assert not er.is_entra(azure) and classify(azure).level == "access"
    assert er.level_of([]) == ("unknown", ())
    assert classify([{"allowedResourceActions": []}]).level == "view"


def test_entra_roles_are_high_risk_when_they_reach_access_or_keys():
    assert classify_permissions(_perms("Global Administrator")) == "high"
    assert classify_permissions(_perms("Helpdesk Administrator")) == "high"
    assert classify_permissions(_perms("Exchange Administrator")) is None
    assert risk_reasons(_perms("Privileged Role Administrator")) == [
        "can change who has access in Entra ID (Entra roles)"
    ]
    assert risk_reasons(_perms("Reports Reader")) == []


# ---- reading them -----------------------------------------------------------------------

GA = BUILTIN["Global Administrator"]["templateId"]
READER = BUILTIN["Global Reader"]["templateId"]


class _Response:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self) -> dict:
        return self._body


class _Client:
    """Answers by URL path; remembers what was asked."""

    def __init__(self, answers: dict[str, list[_Response]]):
        self.answers, self.asked = answers, []

    def get(self, url, params=None, headers=None):
        self.asked.append((url, params))
        key = url.split("/v1.0/", 1)[-1].split("?", 1)[0]
        return self.answers[key].pop(0)


def _ok(*values, next_link: str | None = None) -> _Response:
    body: dict = {"value": list(values)}
    if next_link:
        body["@odata.nextLink"] = next_link
    return _Response(200, body)


def _tenant() -> dict[str, list[_Response]]:
    return {
        "roleManagement/directory/roleDefinitions": [
            _ok(
                {
                    "id": GA,
                    "templateId": GA,
                    "displayName": "Global Administrator",
                    "isBuiltIn": True,
                    "rolePermissions": _perms("Global Administrator"),
                },
                next_link=f"{ed.GRAPH}/roleManagement/directory/roleDefinitions?$skiptoken=2",
            ),
            _ok(
                {
                    "id": READER,
                    "templateId": READER,
                    "displayName": "Global Reader",
                    "isBuiltIn": True,
                    "rolePermissions": _perms("Global Reader"),
                }
            ),
        ],
        "roleManagement/directory/roleAssignmentScheduleInstances": [
            _ok(
                {
                    "principalId": "ADA-ID",
                    "roleDefinitionId": GA,
                    "assignmentType": "Activated",
                    "startDateTime": "2026-11-18T08:00:00Z",
                    "endDateTime": "2026-11-18T10:00:00Z",
                }
            )
        ],
        "roleManagement/directory/roleAssignments": [
            _ok(
                {
                    "id": "ra-1",
                    "principalId": "ADA-ID",
                    "roleDefinitionId": GA,
                    "directoryScopeId": "/",
                    "principal": {
                        "@odata.type": "#microsoft.graph.user",
                        "userPrincipalName": "ada@example.org",
                    },
                },
                {
                    "id": "ra-2",
                    "principalId": "GRP-READERS",
                    "roleDefinitionId": READER,
                    "directoryScopeId": "/",
                    "principal": {
                        "@odata.type": "#microsoft.graph.group",
                        "displayName": "grp-readers",
                    },
                },
                {
                    "id": "ra-4",
                    "principalId": "PARTNER-GRP",
                    "principalOrganizationId": "PARTNER-TENANT",
                    "roleDefinitionId": READER,
                    "directoryScopeId": "/",
                    "principal": {
                        "@odata.type": "#microsoft.graph.group",
                        "displayName": "partner-admins",
                    },
                },
                {
                    "id": "ra-3",
                    "principalId": "SP-1",
                    "roleDefinitionId": "hidden-role",
                    "directoryScopeId": "/",
                    "principal": {
                        "@odata.type": "#microsoft.graph.servicePrincipal",
                        "displayName": "sp-sync",
                    },
                },
            )
        ],
        "roleManagement/directory/roleEligibilitySchedules": [
            _ok(
                {
                    "id": "el-1",
                    "principalId": "BOB-ID",
                    "roleDefinitionId": GA,
                    "directoryScopeId": "/",
                    "memberType": "Direct",
                    "status": "Provisioned",
                    "scheduleInfo": {
                        "startDateTime": "2026-01-01T00:00:00Z",
                        "expiration": {"type": "noExpiration"},
                    },
                    "principal": {
                        "@odata.type": "#microsoft.graph.user",
                        "userPrincipalName": "bob@example.org",
                    },
                },
                {
                    "id": "el-2",
                    "principalId": "GRP-OWN",
                    "roleDefinitionId": READER,
                    "memberType": "Direct",
                    "principal": {
                        "@odata.type": "#microsoft.graph.group",
                        "displayName": "grp-own",
                    },
                },
                {
                    "id": "el-3",
                    "principalId": "PARTNER-ELIGIBLE",
                    "roleDefinitionId": READER,
                    "memberType": "Direct",
                    "principal": {
                        "@odata.type": "#microsoft.graph.group",
                        "displayName": "partner-x",
                    },
                },
                {
                    "id": "el-4",
                    "principalId": "PARTNER-GRP",
                    "roleDefinitionId": GA,
                    "memberType": "Direct",
                    "principal": {
                        "@odata.type": "#microsoft.graph.group",
                        "displayName": "partner-admins",
                    },
                },
            )
        ],
        # Eligible groups: one of the tenant's, one its directory doesn't know (a partner's).
        "groups/grp-own": [_ok()],
        "groups/partner-eligible": [_Response(404)],
        "directoryRoles": [
            _ok({"roleTemplateId": "hidden-role", "displayName": "Hidden Sync Role"})
        ],
    }


def test_reading_the_tenant_gives_grants_and_eligibility_at_the_entra_place():
    client = _Client(_tenant())
    got = ed.read(ed._Graph(client, "token"), tenant_id="tenant-1")
    assert set(got.role_definitions) == {GA.lower(), READER.lower()}
    assert got.role_definitions[GA.lower()]["role_type"] == er.ENTRA_BUILTIN
    by = {a.principal_id: a for a in got.assignments}
    ada = by["ada-id"]
    assert (ada.scope, ada.scope_level, ada.role_name, ada.principal_type) == (
        er.ENTRA_PLACE,
        "directory",
        "Global Administrator",
        "User",
    )
    assert ada.role_definition_id == f"{er.ENTRA_PLACE}/roleDefinitions/{GA.lower()}"
    assert (ada.assignment_type, ada.end_at.hour) == ("Activated", 10)
    assert by["grp-readers"].assignment_type == "Assigned"
    # A partner's group (another tenant, GDAP): typed ForeignGroup, its members never asked for.
    assert by["partner-grp"].principal_type == "ForeignGroup"
    assert by["partner-grp"].raw["principalOrganizationId"] == "partner-tenant"
    assert by["sp-1"].role_name == "Hidden Sync Role"  # named from the legacy list
    eligible = {e.principal_id: e for e in got.eligibilities}
    # A partner's group: known as such from its assignment, or unknown to this directory.
    assert eligible["partner-grp"].principal_type == "ForeignGroup"
    assert eligible["partner-eligible"].principal_type == "ForeignGroup"
    assert eligible["grp-own"].principal_type == "Group"
    assert got.group_ids == {"grp-readers", "grp-own"}  # partners' members are never asked for
    asked = sorted(u for u, _p in client.asked if "/groups/" in u)
    assert asked == [
        f"{ed.GRAPH}/groups/grp-own?$select=id",
        f"{ed.GRAPH}/groups/partner-eligible?$select=id",
    ]
    bob = eligible["bob-id"]
    assert (bob.principal_id, bob.role_name, bob.member_type, bob.end_at) == (
        "bob-id",
        "Global Administrator",
        "Direct",
        None,
    )
    assert "roleManagement/directory/roleDefinitions?$skiptoken=2" in client.asked[1][0]


def test_a_refusal_leaves_the_place_unread_and_graph_down_fails():
    with pytest.raises(ed.EntraNotReadable):
        ed.read(
            ed._Graph(_Client({"roleManagement/directory/roleDefinitions": [_Response(403)]}), "t")
        )
    with pytest.raises(ed.GraphUnavailable):
        ed.read(
            ed._Graph(_Client({"roleManagement/directory/roleDefinitions": [_Response(500)]}), "t")
        )
    assert ed.hierarchy_node(unread=True) == {
        "name": "Entra ID",
        "level": "directory",
        "parent": None,
        "unread": True,
    }


def test_only_a_check_of_the_tenant_root_group_reads_entra():
    claims = base64.urlsafe_b64encode(json.dumps({"tid": "TENANT-1"}).encode()).decode().rstrip("=")
    tid = ed.tenant_of(f"h.{claims}.s")
    assert tid == "tenant-1"
    root = "/providers/Microsoft.Management/managementGroups/tenant-1"
    assert ed.is_tenant_root(root, "mgmt_group", tid)
    assert not ed.is_tenant_root(
        "/providers/microsoft.management/managementgroups/mg-x", "mgmt_group", tid
    )
    assert not ed.is_tenant_root("/subscriptions/tenant-1", "subscription", tid)
    assert ed.tenant_of("not-a-token") is None
