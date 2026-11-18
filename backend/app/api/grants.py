"""``GET /grants/{grant_id}`` — one grant: who, can do what, where, since when, how, used?

The id is ``grant_fingerprint(scope, role, principal)``, stable across
daily checks, so a link to a grant keeps working after the next check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.findings import current_finding
from app.api.scope_capabilities import _KIND
from app.api.scope_views import _condition, _origin
from app.core.advice import advise_grant, context
from app.core.capabilities import LEVEL_RANK, Capability
from app.core.current_view import current_view
from app.core.findings import finding_sort_key
from app.core.operation_names import names_for
from app.core.reviews import period_of
from app.core.security import require_auth, who_is
from app.db.models import GroupMembership, Principal, ReviewItem, ReviewRound, Scan, ScanCoverage
from app.db.session import get_session
from app.schemas.grants import GrantDetail, GrantOperation, GrantOwner, GrantPower
from app.schemas.registry import ScopeCrumb
from app.schemas.reviews import ReviewRef

router = APIRouter(prefix="/grants", tags=["grants"])


def powers_of(cap: Capability) -> list[GrantPower]:
    """The five things a role can let someone do, each allowed or not."""
    rank = LEVEL_RANK[cap.level]
    known = cap.level != "unknown"
    keys = ", ".join(cap.keys_for) if cap.keys_for else None
    data_detail = ("read and change" if cap.data_write else "read only") if cap.data else None
    return [
        GrantPower(key="access", label="Change who has access", allowed=known and rank == 0),
        GrantPower(
            key="keys", label="Get keys and secrets", allowed=known and rank <= 1, detail=keys
        ),
        GrantPower(
            key="change", label="Create, change or delete resources", allowed=known and rank <= 2
        ),
        GrantPower(
            key="data", label="Work with the data inside", allowed=cap.data, detail=data_detail
        ),
        GrantPower(key="view", label="See how things are set up", allowed=known),
    ]


def _review_ref(
    session: Session, fingerprint: str, claims: dict[str, Any] | None
) -> ReviewRef | None:
    period = period_of(datetime.now(UTC))
    row = session.execute(
        select(ReviewItem, ReviewRound)
        .join(ReviewRound, ReviewRound.id == ReviewItem.round_id)
        .where(ReviewRound.period == period, ReviewItem.grant_fingerprint == fingerprint)
        .order_by(ReviewRound.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    item, rnd = row
    oid, _, admin = who_is(session, claims)
    return ReviewRef(
        round_id=rnd.id,
        item_id=item.id,
        period=rnd.period,
        reviewer_name=rnd.reviewer_name,
        decision=item.decision,
        can_decide=rnd.finished_at is None
        and (admin or (oid is not None and oid == rnd.reviewer_id)),
    )


@router.get("/{grant_id}", response_model=GrantDetail)
def grant_detail(
    grant_id: str,
    session: Annotated[Session, Depends(get_session)],
    claims: Annotated[dict[str, Any] | None, Depends(require_auth)] = None,
) -> GrantDetail:
    view = current_view(session)
    ra = view.by_grant_id.get(grant_id.lower())
    if ra is None:
        raise HTTPException(status_code=404, detail="this grant is not in the latest daily check")

    cap = view.capability_of(ra)
    scope = ra.scope.lower()
    kind = _KIND.get(ra.principal_type, "other")
    pid = ra.principal_id.lower()

    member_count = None
    if kind == "group" and pid in view.membership_scan:
        member_count = session.scalar(
            select(func.count())
            .select_from(GroupMembership)
            .where(GroupMembership.scan_id == view.membership_scan[pid])
            .where(func.lower(GroupMembership.group_id) == pid)
        )

    coverage = view.coverage_by_ra.get(ra.id) or {}
    raw_ops = [o for o in coverage.get("top_operations") or [] if o.get("operation_name")][:10]
    op_names = names_for(session, (o["operation_name"] for o in raw_ops))
    cache = session.get(ScanCoverage, ra.scan_id)

    refs = sorted(
        view.findings_by_ra.get(ra.id, ()),
        key=lambda r: finding_sort_key(r.finding.severity, r.finding.category, r.finding.title),
    )

    return GrantDetail(
        assignment_id=ra.id,
        principal_id=ra.principal_id,
        principal_name=ra.principal_name,
        principal_type=ra.principal_type,
        kind=kind,
        member_count=member_count,
        role_name=ra.role_name,
        role_definition_id=ra.role_definition_id,
        role_type=ra.role_type,
        level=cap.level,
        powers=powers_of(cap),
        scope=ra.scope,
        scope_name=view.name.get(scope) or ra.scope_name,
        scope_level=ra.scope_level,
        ancestors=[
            ScopeCrumb(id=a, name=view.name.get(a), level=view.level.get(a, "mgmt_group"))
            for a in reversed(view.ancestors(scope))
        ],
        inside_count=max(0, len(view.subtree(scope)) - 1) if scope in view.parent else 0,
        activated=ra.assignment_type == "Activated",
        until=ra.end_at,
        access_as_of=session.scalar(select(Scan.finished_at).where(Scan.id == ra.scan_id)),
        last_activity_at=session.scalar(
            select(Principal.last_activity_at).where(Principal.id == pid)
        ),
        window_days=cache.window_days if cache else None,
        observed_op_count=coverage.get("observed_op_count"),
        top_operations=[
            GrantOperation(
                operation=o["operation_name"],
                display=op_names.get(o["operation_name"], o["operation_name"]),
                count=int(o.get("count") or 0),
            )
            for o in raw_ops
        ],
        findings=[current_finding(ra, r) for r in refs],
        review=_review_ref(session, grant_id.lower(), claims),
        advice=advise_grant(context(session, view), ra),
        **_owners(session, ra),
        **_condition(view, ra),
        **_origin(ra),
    )


def _owners(session: Session, ra) -> dict:
    """``owners`` / ``owners_elsewhere`` for an app's or group's grant."""
    if ra.principal_type not in ("ServicePrincipal", "Group"):
        return {}
    from app.core.owners import current_owners

    info = current_owners(session, {ra.principal_id})[ra.principal_id.lower()]
    if not info.known:
        return {"owners_elsewhere": info.registered_elsewhere}
    vias: dict[str, list[str]] = {}
    names: dict[str, tuple[str | None, str | None]] = {}
    for o in info.owners:
        vias.setdefault(o.owner_id, []).append(o.via)
        names[o.owner_id] = (o.owner_name, o.owner_type)
    return {
        "owners": [
            GrantOwner(
                owner_id=k, owner_name=names[k][0], owner_type=names[k][1], via=sorted(set(v))
            )
            for k, v in vias.items()
        ],
        "owners_elsewhere": info.registered_elsewhere,
    }
