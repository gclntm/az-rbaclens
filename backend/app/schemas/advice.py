"""Advice: what to do about a grant or an identity (core/advice.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Weight = Literal["must", "should", "could", "info"]
Action = Literal[
    "remove", "pim", "replace", "narrow", "ask", "fix_members", "set_responsible", "keep"
]
SuggestedDecision = Literal["keep", "remove", "change", "ask"]


class AdviceNote(BaseModel):
    """Another rule that fired on the same grant, shown under the main advice."""

    rule: str
    weight: Weight
    title: str


class Advice(BaseModel):
    """One grant: one action, from one rule of the playbook."""

    rule: str  # "R02 standing-admin"
    weight: Weight
    action: Action
    # The review decision this advice suggests; None for admin tasks.
    decision: SuggestedDecision | None
    title: str  # the verdict in a few words
    why: str  # one sentence built from the facts
    how: list[str] = Field(default_factory=list)  # steps, plain words
    commands: list[str] = Field(default_factory=list)  # ready-to-run, RBAC Lens never runs them
    controls: list[str] = Field(default_factory=list)  # ISO/IEC 27001:2022 Annex A
    also: list[AdviceNote] = Field(default_factory=list)


class AdviceGrantRef(BaseModel):
    grant_id: str
    role_name: str | None
    scope: str
    scope_name: str | None
    scope_level: str
    # This grant's own commands, so a filtered list never shows another place's.
    commands: list[str] = Field(default_factory=list)


class IdentityAction(BaseModel):
    """One line of "What to do" for a person, group or app: one rule, its grants."""

    rule: str
    weight: Weight
    action: Action
    decision: SuggestedDecision | None
    title: str
    why: str
    how: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    grants: list[AdviceGrantRef] = Field(default_factory=list)


class IdentityAdvice(BaseModel):
    actions: list[IdentityAction]
    # Actions not shown (beyond the first ``limit``).
    more: int = 0
    # Grants of this identity with nothing to do (R00 or R15 only).
    fine: int = 0


class AdviceSettings(BaseModel):
    """Thresholds and exemptions; admins only."""

    new_days: int = Field(30, ge=1, le=365)
    expiring_days: int = Field(14, ge=1, le=180)
    # Emergency identities: exempt from the standing-admin rules (R02, R03).
    break_glass: list[str] = Field(default_factory=list, max_length=50)


class BreakGlassRead(BaseModel):
    principal_id: str
    name: str | None


class AdviceSettingsRead(AdviceSettings):
    break_glass_names: list[BreakGlassRead] = Field(default_factory=list)
