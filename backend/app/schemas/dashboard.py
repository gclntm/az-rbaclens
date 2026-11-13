"""Dashboard / overview schemas."""

from datetime import datetime

from pydantic import BaseModel

# --------------------------------------------------------------------------------------
# /dashboard/overview — current-state KPIs (see app.core.overview)
# --------------------------------------------------------------------------------------


class OverviewKpis(BaseModel):
    assignments: int
    principals: int
    # Current RAs with a high-risk role at MG or subscription scope.
    high_risk_broad: int
    # Open ``unused`` findings on current RAs.
    unused: int
    # Distinct current principals Graph could not name.
    unresolved_principals: int
    open_findings: int
    # Change vs the previous snapshot of each root, for roots whose
    # latest snapshot finished in the last 30 days.
    added_30d: int
    removed_30d: int
    # Open findings that need a look: everything except ``unused``, which
    # is evidence (Azure never logs reads), not a problem on its own.
    needs_look: int = 0
    # Identities (people, groups, apps) by the strongest thing any of their
    # current grants lets them do, anywhere: {access, keys, change, data, view, unknown}.
    by_level: dict[str, int] = {}


class AttentionItem(BaseModel):
    finding_id: int
    fingerprint: str
    scan_id: int
    category: str
    severity: str
    title: str
    detail: str
    suggestion: str | None
    scope: str
    scope_name: str | None
    scope_level: str
    principal_id: str
    principal_name: str | None = None
    role_name: str | None = None
    # ``/grants/{grant_id}``.
    grant_id: str | None = None


class TopRole(BaseModel):
    role_name: str | None
    role_definition_id: str
    role_risk: str | None
    count: int


class StaleScope(BaseModel):
    id: str
    name: str | None
    level: str
    age_days: int | None


class Freshness(BaseModel):
    window_days: int
    subscriptions_total: int
    subscriptions_fresh: int
    stale: list[StaleScope]


class JobRow(BaseModel):
    kind: str  # "snapshot" | "activity_pull"
    id: int
    target: str
    target_name: str | None
    status: str
    progress_done: int
    progress_total: int | None
    progress_message: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class Overview(BaseModel):
    kpis: OverviewKpis
    # Newest finish time among the checks behind the current picture. Places
    # not checked recently are listed in ``freshness.stale``.
    access_as_of: datetime | None = None
    # Newest Activity Log event collected.
    activity_up_to: datetime | None = None
    attention: list[AttentionItem]
    top_roles: list[TopRole]
    freshness: Freshness
    jobs: list[JobRow]
    # Snapshots contributing to the current picture whose findings were
    # never computed (scans that pre-date findings). Finding-based KPIs
    # under-report until these are recomputed.
    findings_pending: list[int] = []
