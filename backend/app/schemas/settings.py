"""Pydantic schemas for the settings API."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _guid(value: str) -> str:
    """An Entra object or tenant id, lowercased."""
    value = value.strip().lower()
    if not _GUID.match(value):
        raise ValueError("must be a GUID, e.g. 00000000-0000-0000-0000-000000000000")
    return value


CredentialSource = Literal["env", "db", "default", "none"]


class CredentialsRead(BaseModel):
    """Public-facing view of the current Azure credential state.

    Never includes the client_secret itself — only ``client_secret_set``
    indicates whether one is configured.
    """

    tenant_id: str | None = None
    client_id: str | None = None
    client_secret_set: bool = False
    source: CredentialSource = "none"
    updated_at: datetime | None = None
    encryption_available: bool = False


class CredentialsWrite(BaseModel):
    """Update payload. Empty / null fields are not modified.

    To clear the secret, send ``client_secret=""``.
    """

    tenant_id: str | None = Field(None, max_length=255)
    client_id: str | None = Field(None, max_length=255)
    client_secret: str | None = Field(None, max_length=2000)


class AuthConfigPublic(BaseModel):
    """What the SPA needs to bootstrap MSAL.

    Served by an unauthenticated endpoint (chicken-and-egg: the browser
    has to know whether to log in *before* it can log in). Contains no
    secrets — tenant_id and client_id are public identifiers.
    """

    enabled: bool = False
    tenant_id: str | None = None
    client_id: str | None = None
    authority: str | None = None
    """Convenience: ``https://login.microsoftonline.com/<tenant_id>``."""


class AuthSettingsRead(BaseModel):
    """Full auth config returned by the protected settings endpoint."""

    enabled: bool = False
    tenant_id: str | None = None
    client_id: str | None = None
    allowed_group_ids: list[str] = Field(default_factory=list)
    admin_group_ids: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AuthSettingsWrite(BaseModel):
    """Update payload for the auth settings tab."""

    enabled: bool | None = None
    # Entra ids are GUIDs; an empty string clears the value.
    tenant_id: str | None = Field(None, max_length=255)
    client_id: str | None = Field(None, max_length=255)
    allowed_group_ids: list[str] | None = Field(None, max_length=100)
    admin_group_ids: list[str] | None = Field(None, max_length=100)

    @field_validator("tenant_id", "client_id")
    @classmethod
    def _guid_or_empty(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        return _guid(value)

    @field_validator("allowed_group_ids", "admin_group_ids")
    @classmethod
    def _guids(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [_guid(v) for v in value if v.strip()]
