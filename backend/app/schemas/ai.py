"""Payloads of the optional AI wording (core/ai_wording.py)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AiSettingsRead(BaseModel):
    enabled: bool
    model: str
    models: list[str]
    # Where the API key comes from; the key itself is never returned.
    key_source: Literal["stored", "environment"] | None
    encryption_available: bool
    # New texts per day (UTC) at most, 0 = no limit; and how many were written today.
    daily_limit: int = 0
    written_today: int = 0


class AiSettingsWrite(BaseModel):
    enabled: bool
    model: str = Field(max_length=64)
    # None keeps the stored key; "" clears it; anything else replaces it.
    api_key: str | None = Field(default=None, max_length=500)


class AiStatus(BaseModel):
    available: bool
    model: str | None


class AiTarget(BaseModel):
    """One grant or one identity, exactly one of the two."""

    grant_id: str | None = Field(default=None, max_length=64)
    principal_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def one_of(self) -> "AiTarget":
        if bool(self.grant_id) == bool(self.principal_id):
            raise ValueError("give a grant_id or a principal_id")
        return self


class AiPreview(BaseModel):
    """Exactly what would be sent, and to which model."""

    model: str
    system: str
    facts: dict[str, Any]


class AiExplanation(BaseModel):
    text: str
    model: str
    cached: bool
