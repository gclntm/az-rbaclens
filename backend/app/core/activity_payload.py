"""What we keep of an Activity Log event's SDK payload (``activity_events.raw``).

The full payload is a few KB per event, most of the table's size, and
the app reads one value from it: the caller's object id. Two rules keep
the rest proportionate:

- **Drop keys already stored as columns** (``operation_name``, ``status``,
  ``resource_id``, …) and Azure's own event ARM id.
- **Keep a claims subset.** ``claims`` is the caller's whole JWT — 35 % of
  the payload, mostly token ids, timestamps and ``xms_*`` flags. The
  subset keeps who called and from where.

Everything else (``authorization``, ``properties``, ``http_request``,
``description``, …) stays for the event drawer. Migration 0022 applies
the same rules in SQL to existing rows; change both together.
"""

from __future__ import annotations

from typing import Any

OID_CLAIM = "http://schemas.microsoft.com/identity/claims/objectidentifier"
UPN_CLAIM = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn"

# Top-level payload keys that duplicate an ``activity_events`` column (or,
# for ``id``, are derivable from ``resource_id`` + ``event_data_id``).
DROPPED_KEYS = (
    "id",
    "event_data_id",
    "subscription_id",
    "caller",
    "operation_name",
    "status",
    "resource_id",
    "resource_type",
    "correlation_id",
    "event_timestamp",
)

# ``{kept name: source claim names, first present wins}``. ``oid`` is also
# the short claim Azure itself uses, so a slimmed payload slims to itself.
CLAIM_SUBSET: dict[str, tuple[str, ...]] = {
    "oid": (OID_CLAIM, "oid"),
    "appid": ("appid",),
    "idtyp": ("idtyp",),
    "upn": (UPN_CLAIM, "upn"),
    "name": ("name",),
    "ipaddr": ("ipaddr",),
}


def _first(claims: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = claims.get(name)
        if value not in (None, ""):
            return value
    return None


def caller_oid_from_payload(raw: dict[str, Any] | None) -> str | None:
    """The caller's object id (lowercased) from a full or slimmed payload."""
    claims = (raw or {}).get("claims")
    if not isinstance(claims, dict):
        return None
    oid = _first(claims, CLAIM_SUBSET["oid"])
    return str(oid).lower() if oid else None


def slim_payload(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Apply the module's rules; idempotent, returns a new dict."""
    if raw is None:
        return None
    out = {k: v for k, v in raw.items() if k not in DROPPED_KEYS and k != "claims"}
    claims = raw.get("claims")
    if isinstance(claims, dict):
        subset = {
            key: value
            for key, names in CLAIM_SUBSET.items()
            if (value := _first(claims, names)) is not None
        }
        out["claims"] = subset
    return out
