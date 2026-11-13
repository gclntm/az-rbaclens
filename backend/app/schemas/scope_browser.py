"""Pydantic schemas for the scan-helper scope browser."""

from pydantic import BaseModel


class MgmtGroupNode(BaseModel):
    """One management group visible to the SP.

    ``depth`` is precomputed so the UI can render an indented dropdown
    without re-walking the parent chain. ``scope`` is the full ARM path
    (lower-cased to match what scans store).
    """

    id: str  # name segment of the ARM id, e.g. "mg-platform"
    display_name: str
    parent_id: str | None  # name segment of the parent MG, or None for root
    depth: int
    scope: str


class SubscriptionNode(BaseModel):
    id: str  # subscription GUID
    display_name: str
    scope: str
    mgmt_group_id: str | None  # parent MG name segment, if known
