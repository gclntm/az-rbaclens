"""The current owners of the apps and groups that hold access (plan "owners").

Like a group's members, an object's owners are those read by the newest
completed check that read them (``scan_owner_resolutions``). An object no
check could read has unknown owners (``known`` false), which is not the
same as none.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import AppCredential, PrincipalOwner, Scan, ScanOwnerResolution

_CHUNK = 500


@dataclass(frozen=True)
class OwnerRef:
    owner_id: str
    owner_name: str | None
    owner_type: str | None
    via: str  # service_principal | registration | group


@dataclass
class ObjectOwners:
    known: bool = False
    registered_elsewhere: bool = False
    app_id: str | None = None
    owners: list[OwnerRef] = field(default_factory=list)

    @property
    def people(self) -> list[OwnerRef]:
        """Each owner once, whatever the way (an app's registration and its service principal)."""
        seen: dict[str, OwnerRef] = {}
        for o in self.owners:
            seen.setdefault(o.owner_id, o)
        return list(seen.values())


def _newest(session: Session, object_ids: set[str]) -> dict[str, tuple[int, bool, str | None]]:
    """``{object: (scan id, registered elsewhere, app id)}`` of the newest completed
    check that read each object's owners."""
    ids = sorted(object_ids)
    newest: dict[str, tuple[int, bool, str | None]] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        latest = (
            select(
                ScanOwnerResolution.object_id,
                func.max(ScanOwnerResolution.scan_id).label("scan_id"),
            )
            .join(Scan, Scan.id == ScanOwnerResolution.scan_id)
            .where(Scan.status == "done", ScanOwnerResolution.object_id.in_(chunk))
            .group_by(ScanOwnerResolution.object_id)
            .subquery()
        )
        for object_id, scan_id, elsewhere, app_id in session.execute(
            select(
                ScanOwnerResolution.object_id,
                ScanOwnerResolution.scan_id,
                ScanOwnerResolution.registered_elsewhere,
                ScanOwnerResolution.app_id,
            ).join(
                latest,
                (latest.c.object_id == ScanOwnerResolution.object_id)
                & (latest.c.scan_id == ScanOwnerResolution.scan_id),
            )
        ).all():
            newest[object_id] = (scan_id, bool(elsewhere), app_id)
    return newest


@dataclass(frozen=True)
class CredentialRef:
    via: str  # registration | service_principal
    kind: str  # secret | certificate
    key_id: str
    start_at: datetime | None
    end_at: datetime | None


def current_credentials(session: Session, object_ids: set[str]) -> dict[str, list[CredentialRef]]:
    """``{app: [its secrets and certificates]}`` from the newest check that read it;
    an app no check read is missing."""
    newest = _newest(session, {o.lower() for o in object_ids})
    out: dict[str, list[CredentialRef]] = {o: [] for o in newest}
    by_scan: dict[int, list[str]] = defaultdict(list)
    for object_id, (scan_id, _e, _a) in newest.items():
        by_scan[scan_id].append(object_id)
    for scan_id, objects in by_scan.items():
        for i in range(0, len(objects), _CHUNK):
            for c in session.scalars(
                select(AppCredential)
                .where(AppCredential.scan_id == scan_id)
                .where(AppCredential.object_id.in_(objects[i : i + _CHUNK]))
                .order_by(AppCredential.end_at)
            ).all():
                out[c.object_id].append(
                    CredentialRef(c.via, c.kind, c.key_id, as_utc(c.start_at), as_utc(c.end_at))
                )
    return out


def current_owners(session: Session, object_ids: set[str]) -> dict[str, ObjectOwners]:
    """``{object id: ObjectOwners}`` for each of ``object_ids`` (lowercased)."""
    ids = sorted({o.lower() for o in object_ids})
    out: dict[str, ObjectOwners] = {i: ObjectOwners() for i in ids}
    if not ids:
        return out
    newest = _newest(session, set(ids))
    by_scan: dict[int, list[str]] = defaultdict(list)
    for object_id, (scan_id, elsewhere, app_id) in newest.items():
        out[object_id] = ObjectOwners(known=True, registered_elsewhere=elsewhere, app_id=app_id)
        by_scan[scan_id].append(object_id)
    for scan_id, objects in by_scan.items():
        for i in range(0, len(objects), _CHUNK):
            for row in session.scalars(
                select(PrincipalOwner)
                .where(PrincipalOwner.scan_id == scan_id)
                .where(PrincipalOwner.object_id.in_(objects[i : i + _CHUNK]))
                .order_by(PrincipalOwner.owner_name)
            ).all():
                out[row.object_id].owners.append(
                    OwnerRef(row.owner_id, row.owner_name, row.owner_type, row.via)
                )
    return out


def owned_by(session: Session, owner_id: str) -> dict[str, list[str]]:
    """``{object id: [via, …]}`` the identity currently owns."""
    oid = owner_id.lower()
    candidates = {
        o
        for (o,) in session.execute(
            select(PrincipalOwner.object_id).where(PrincipalOwner.owner_id == oid).distinct()
        ).all()
    }
    out: dict[str, list[str]] = {}
    for object_id, info in current_owners(session, candidates).items():
        vias = sorted({o.via for o in info.owners if o.owner_id == oid})
        if vias:
            out[object_id] = vias
    return out
