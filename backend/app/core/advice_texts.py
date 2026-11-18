"""The playbook's fixed parts: one entry per rule, and the "how" builders.

Rule ids and wording are part of the audit trail: change a text freely,
but never reuse an id for a different rule. See docs/api.md (Advice and AI wording).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.core.entra_roles import ENTRA_PLACE
from app.schemas.advice import Action, SuggestedDecision, Weight

# ISO/IEC 27001:2022 Annex A controls named by the rules and the report.
CONTROLS: dict[str, str] = {
    "5.15": "Access control",
    "5.16": "Identity management",
    "5.17": "Authentication information",
    "5.18": "Access rights",
    "8.2": "Privileged access rights",
    "8.15": "Logging",
}


@dataclass(frozen=True)
class Rule:
    id: str
    weight: Weight
    action: Action
    decision: SuggestedDecision | None
    title: str
    controls: tuple[str, ...]


RULES: dict[str, Rule] = {
    r.id: r
    for r in (
        Rule(
            "R01 orphan",
            "must",
            "remove",
            "remove",
            "Remove: the identity no longer exists",
            ("5.16", "5.18"),
        ),
        Rule(
            "R02 standing-admin",
            "must",
            "pim",
            "change",
            "Make it PIM-eligible: standing power to change access",
            ("8.2",),
        ),
        Rule(
            "R03 idle-admin",
            "must",
            "remove",
            "remove",
            "Remove: power to change access, not used",
            ("8.2", "5.18"),
        ),
        Rule(
            "R04 guest-power",
            "must",
            "ask",
            "ask",
            "Ask the sponsor: a guest with power",
            ("5.16", "8.2"),
        ),
        Rule(
            "R05 keys-broad",
            "should",
            "pim",
            "change",
            "Narrow or make PIM-eligible: keys and passwords, high up",
            ("5.17", "8.2"),
        ),
        Rule(
            "R06 over-broad",
            "should",
            "replace",
            "change",
            "Replace with a smaller role",
            ("5.15",),
        ),
        Rule(
            "R07 redundant",
            "should",
            "remove",
            "remove",
            "Remove: already covered from above",
            ("5.15",),
        ),
        Rule(
            "R08 direct-user",
            "should",
            "remove",
            "remove",
            "Remove the direct grant: a group gives the same",
            ("5.15", "5.18"),
        ),
        Rule(
            "R09 app-owner",
            "should",
            "replace",
            "change",
            "Replace: an app that can change access",
            ("8.2",),
        ),
        Rule(
            "R10 custom-replaceable",
            "could",
            "replace",
            "change",
            "Replace the custom role with a built-in one",
            ("5.15",),
        ),
        Rule(
            "R11 unknown-members",
            "should",
            "fix_members",
            "ask",
            "Find out who is in the group",
            ("5.18",),
        ),
        Rule(
            "R12 new-unexplained",
            "should",
            "ask",
            "ask",
            "Confirm with whoever gave it",
            ("5.18", "8.15"),
        ),
        Rule(
            "R13 no-responsible",
            "should",
            "set_responsible",
            None,
            "Nobody is responsible for this place",
            ("5.18",),
        ),
        Rule(
            "R14 expiring",
            "could",
            "keep",
            "keep",
            "Ends soon: extend it only on purpose",
            ("5.18",),
        ),
        # Before R15 so it wins between the two "keep" rules (the order below is the tie-break).
        Rule(
            "R16 microsoft-app",
            "info",
            "keep",
            "keep",
            "Microsoft's own app for an Azure service",
            ("5.15",),
        ),
        Rule("R15 limited", "info", "keep", "keep", "Limited by a condition", ("5.15",)),
        Rule("R00 fine", "info", "keep", "keep", "Nothing to do", ()),
    )
}

# Highest weight wins, then the playbook order.
WEIGHT_RANK: dict[str, int] = {"must": 0, "should": 1, "could": 2, "info": 3}
ORDER: dict[str, int] = {rid: i for i, rid in enumerate(RULES)}

PIM_PORTAL = (
    "Entra ID › Privileged Identity Management › Azure resources › {place} › Roles › "
    "{role} › Add assignments › Assignment type: Eligible"
)


def _q(value: str) -> str:
    """Quote one value for a POSIX shell. Names come from Azure and Entra ID, where
    whoever creates an object picks its name, so the value is never interpreted:
    single quotes, the standard library's way (``shlex.quote``)."""
    return shlex.quote(value)


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def comment_text(value: str) -> str:
    """Text safe on one ``#`` comment line of a script: no line breaks or control characters."""
    return _CONTROL.sub(" ", value)


ENTRA_ASSIGNMENTS = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments"
ENTRA_PORTAL = "Or in the Entra admin center: Roles and administrators › {role} › Assignments."
ENTRA_PIM_PORTAL = (
    "Entra ID › Privileged Identity Management › Microsoft Entra roles › Roles › "
    "{role} › Add assignments › Assignment type: Eligible"
)


def delete_command(
    principal_id: str, role: str, scope: str, entra_assignment_id: str | None = None
) -> str:
    if scope == ENTRA_PLACE:
        return entra_delete_command(entra_assignment_id, principal_id, role)
    return f"az role assignment delete --assignee {_q(principal_id)} --role {_q(role)} --scope {_q(scope)}"


def entra_delete_command(assignment_id: str | None, principal_id: str, role: str) -> str:
    """Remove an Entra role assignment through Microsoft Graph (``az rest``).

    Without the assignment's id (an older record), a comment says where to do
    it instead: Graph deletes only by id.
    """
    if assignment_id:
        return f"az rest --method DELETE --url {_q(f'{ENTRA_ASSIGNMENTS}/{assignment_id}')}"
    who = comment_text(principal_id)
    return f"# Entra admin center › Roles and administrators › {comment_text(role)} › Assignments: remove {who}"


_CLI_TYPE = {"User": "User", "Group": "Group", "ForeignGroup": "ForeignGroup"}


def create_command(principal_id: str, principal_type: str, role: str, scope: str) -> str:
    """``--assignee-object-id`` with its type, so the CLI needs no Graph lookup."""
    kind = _CLI_TYPE.get(principal_type, "ServicePrincipal")
    return (
        f"az role assignment create --assignee-object-id {_q(principal_id)} "
        f"--assignee-principal-type {kind} --role {_q(role)} --scope {_q(scope)}"
    )
