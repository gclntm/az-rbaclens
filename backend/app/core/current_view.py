"""The *current* RBAC picture across all snapshots.

Scans overlap: an old MG-rooted scan and a newer subscription-rooted
scan both contain prod's role assignments. Counting "the latest scan
per root" therefore mixes superseded rows into current ones (and double
counts). The rule used everywhere the UI says "now":

    An RA is current iff it comes from the snapshot that currently owns
    its scope — ``scopes.latest_scan_id`` of the nearest *registered*
    ancestor-or-self of ``ra.scope``.

Scopes no scan has walked (e.g. Tenant Root Group, seen only as the
source of inherited RAs) have no owner snapshot; their RAs are taken
from the newest completed scan that recorded them.

``CurrentView`` builds that set together with the scope tree, role risk,
per-RA coverage and per-RA findings, so the scope tree, scope page,
principal page and overview all agree. Get it with :func:`current_view`:
built once per session (one request), forgotten on any write in it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.capabilities import LEVEL_RANK, UNKNOWN, Capability, classify
from app.core.conditions import ConditionSummary, summarise
from app.core.findings import grant_fingerprint
from app.core.role_risk import classify_permissions
from app.core.scan_coverage import latest_done_scan_ids
from app.db.models import (
    Finding,
    GroupMembership,
    ReviewDecision,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scan,
    ScanCoverage,
    ScanGroupResolution,
    Scope,
)
from app.schemas.findings import ReviewDecisionRead, finding_status

# Entra ID (``directory``) counts: its roles apply to the whole tenant (core/findings.py).
BROAD_LEVELS = frozenset({"directory", "mgmt_group", "subscription"})
_IN_CHUNK = 500


def _chunked(ids: list[int]) -> Iterable[list[int]]:
    for i in range(0, len(ids), _IN_CHUNK):
        yield ids[i : i + _IN_CHUNK]


def role_risk_by_arm(session: Session, arm_ids: set[str]) -> dict[str, str | None]:
    """``{full ARM role-definition id: "high" | None}``."""
    from app.tools.scope_ids import role_definition_guid

    if not arm_ids:
        return {}
    by_guid: dict[str, list[str]] = defaultdict(list)
    for arm in arm_ids:
        by_guid[role_definition_guid(arm)].append(arm)
    out: dict[str, str | None] = dict.fromkeys(arm_ids)
    for guid, perms in session.execute(
        select(RoleDefinition.role_definition_id, RoleDefinition.permissions).where(
            RoleDefinition.role_definition_id.in_(by_guid.keys())
        )
    ).all():
        risk = classify_permissions(perms)
        for arm in by_guid.get(guid, ()):
            out[arm] = risk
    return out


def grant_id(ra: RoleAssignment) -> str:
    return grant_fingerprint(ra.scope, ra.role_definition_id, ra.principal_id)


def owner_of(scope: str, known: set[str] | dict[str, Any]) -> str | None:
    """Nearest registered ancestor-or-self of ``scope`` by path trimming.

    Path trimming is enough here: unregistered scopes are resources the
    walk never enumerated, and those nest by path under their RG.
    """
    cur = scope.lower()
    while cur:
        if cur in known:
            return cur
        if "/" not in cur:
            return None
        cur = cur.rsplit("/", 1)[0]
    return None


@dataclass
class FindingRef:
    finding: Finding
    status: str
    decision: ReviewDecisionRead | None


class CurrentView:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.pointer: dict[str, int | None] = {}
        self.parent: dict[str, str | None] = {}
        self.name: dict[str, str | None] = {}
        self.level: dict[str, str] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        for sid, parent, latest, name, level in session.execute(
            select(Scope.id, Scope.parent_id, Scope.latest_scan_id, Scope.name, Scope.level)
        ).all():
            self.pointer[sid] = latest
            self.parent[sid] = parent
            self.name[sid] = name
            self.level[sid] = level
            if parent:
                self.children[parent].append(sid)

        self.ras: list[RoleAssignment] = self._current_assignments()
        self.by_id: dict[int, RoleAssignment] = {ra.id: ra for ra in self.ras}
        self.owner: dict[int, str | None] = {
            ra.id: owner_of(ra.scope, self.pointer) for ra in self.ras
        }
        self.risk: dict[str, str | None] = role_risk_by_arm(
            session, {ra.role_definition_id for ra in self.ras}
        )

    # ---- current set -------------------------------------------------------------

    def _current_assignments(self) -> list[RoleAssignment]:
        return self._current_rows(select(RoleAssignment), RoleAssignment)

    @cached_property
    def eligibilities(self) -> list[RoleEligibility]:
        """Current PIM eligible assignments — same owning-snapshot rule as RAs."""
        return self._current_rows(select(RoleEligibility), RoleEligibility)

    def _current_rows(self, stmt, model) -> list:
        candidates = {p for p in self.pointer.values() if p is not None}
        candidates |= latest_done_scan_ids(self.session)
        if not candidates:
            return []
        rows = list(self.session.scalars(stmt.where(model.scan_id.in_(candidates))).all())
        keep: list = []
        orphans: list = []
        newest_for_scope: dict[str, int] = {}
        for ra in rows:
            owner = owner_of(ra.scope, self.pointer)
            snapshot = self.pointer.get(owner) if owner else None
            if snapshot is not None:
                if ra.scan_id == snapshot:
                    keep.append(ra)
                continue
            scope = ra.scope.lower()
            orphans.append(ra)
            if ra.scan_id > newest_for_scope.get(scope, -1):
                newest_for_scope[scope] = ra.scan_id
        keep.extend(ra for ra in orphans if ra.scan_id == newest_for_scope[ra.scope.lower()])
        return keep

    # ---- tree helpers ------------------------------------------------------------

    def ancestors(self, scope_id: str) -> list[str]:
        """Closest first."""
        out: list[str] = []
        seen: set[str] = set()
        cur = self.parent.get(scope_id)
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def subtree(self, scope_id: str) -> set[str]:
        out = {scope_id}
        stack = [scope_id]
        while stack:
            for child in self.children.get(stack.pop(), ()):
                if child not in out:
                    out.add(child)
                    stack.append(child)
        return out

    def classify(
        self, ra: RoleAssignment, scope_id: str, ancestors: set[str], subtree: set[str]
    ) -> str | None:
        """``direct`` | ``inherited`` | ``below`` relative to ``scope_id``, or None."""
        s = ra.scope.lower()
        if s == scope_id:
            return "direct"
        if s in ancestors or s == "/":
            return "inherited"
        if self.owner.get(ra.id) in subtree:
            return "below"
        return None

    # ---- groups ------------------------------------------------------------------

    @cached_property
    def group_scans(self) -> dict[str, set[int]]:
        """``{group_id: {scan_id}}`` for groups that currently hold an RA."""
        out: dict[str, set[int]] = defaultdict(set)
        for ra in self.ras:
            if ra.principal_type == "Group":
                out[ra.principal_id.lower()].add(ra.scan_id)
        return out

    @cached_property
    def membership_scan(self) -> dict[str, int]:
        """``{group_id: scan_id}`` — the newest completed scan that resolved
        the membership of each group currently holding an RA.

        Membership is a property of the group, not of the snapshot that owns
        the RA: a tenant-wide snapshot may own the grant while a later, narrower
        one is the only scan that read the group from Graph. A resolution row
        (possibly zero members) or any membership row counts as "resolved".
        """
        # Groups holding a current RA, plus groups that are only PIM-eligible.
        gids = list(
            set(self.group_scans)
            | {e.principal_id.lower() for e in self.eligibilities if e.principal_type == "Group"}
        )
        out: dict[str, int] = {}
        if not gids:
            return out
        for model in (ScanGroupResolution, GroupMembership):
            gid = func.lower(model.group_id)
            for g, scan_id in self.session.execute(
                select(gid, func.max(model.scan_id))
                .join(Scan, Scan.id == model.scan_id)
                .where(Scan.status == "done", gid.in_(gids))
                .group_by(gid)
            ).all():
                if scan_id is not None and scan_id > out.get(g, -1):
                    out[g] = scan_id
        return out

    def groups_of(self, member_ids: set[str]) -> dict[str, set[str]]:
        """``{member_id: {group_id}}`` for groups that currently hold an RA,
        from each group's newest membership snapshot (:attr:`membership_scan`)."""
        if not self.membership_scan or not member_ids:
            return {}
        out: dict[str, set[str]] = {}
        for gid, mid, scan_id in self.session.execute(
            select(GroupMembership.group_id, GroupMembership.member_id, GroupMembership.scan_id)
            .where(GroupMembership.scan_id.in_(set(self.membership_scan.values())))
            .where(func.lower(GroupMembership.member_id).in_({m.lower() for m in member_ids}))
        ).all():
            g = gid.lower()
            if self.membership_scan.get(g) == scan_id:
                out.setdefault(mid.lower(), set()).add(g)
        return out

    # ---- PIM ------------------------------------------------------------------------

    @cached_property
    def eligibility_status(self) -> dict[int, tuple[str | None, str | None]]:
        """``{scan_id: (eligibility_status, eligibility_error)}`` for every
        snapshot that currently owns something."""
        ids = {p for p in self.pointer.values() if p is not None} | {ra.scan_id for ra in self.ras}
        if not ids:
            return {}
        return {
            sid: (status, error)
            for sid, status, error in self.session.execute(
                select(Scan.id, Scan.eligibility_status, Scan.eligibility_error).where(
                    Scan.id.in_(ids)
                )
            ).all()
        }

    def classify_scope(
        self, scope: str, scope_id: str, ancestors: set[str], subtree: set[str]
    ) -> str | None:
        """``direct`` | ``inherited`` | ``below`` for any row with a ``scope``."""
        s = scope.lower()
        if s == scope_id:
            return "direct"
        if s in ancestors or s == "/":
            return "inherited"
        if owner_of(s, self.pointer) in subtree:
            return "below"
        return None

    # ---- conditions -----------------------------------------------------------------

    @cached_property
    def role_names(self) -> dict[str, str]:
        """``{role GUID: name}`` for every cached role — conditions can name
        roles nobody currently holds."""
        return {
            guid.lower(): name
            for guid, name in self.session.execute(
                select(RoleDefinition.role_definition_id, RoleDefinition.role_name)
            ).all()
            if name
        }

    def condition_of(self, ra: RoleAssignment) -> ConditionSummary | None:
        return summarise(ra.condition, self.role_names) if ra.condition else None

    # ---- grants -------------------------------------------------------------------

    @cached_property
    def capabilities(self) -> dict[str, Capability]:
        """``{role GUID: Capability}`` for every cached role definition."""
        return {
            guid.lower(): classify(permissions)
            for guid, permissions in self.session.execute(
                select(RoleDefinition.role_definition_id, RoleDefinition.permissions)
            ).all()
        }

    def capability_of(self, row: RoleAssignment | RoleEligibility) -> Capability:
        guid = row.role_definition_id.rsplit("/", 1)[-1].lower()
        return self.capabilities.get(guid, UNKNOWN)

    @cached_property
    def strongest_level(self) -> dict[str, str]:
        """Each identity's strongest capability level across its own current grants."""
        out: dict[str, str] = {}
        for ra in self.ras:
            level = self.capability_of(ra).level
            pid = ra.principal_id.lower()
            if pid not in out or LEVEL_RANK[level] < LEVEL_RANK[out[pid]]:
                out[pid] = level
        return out

    @cached_property
    def by_grant_id(self) -> dict[str, RoleAssignment]:
        """Current RAs by ``grant_fingerprint`` — the id the Grant page uses."""
        return {grant_id(ra): ra for ra in self.ras}

    def is_high_risk(self, ra: RoleAssignment) -> bool:
        return self.risk.get(ra.role_definition_id) == "high" and ra.scope_level in BROAD_LEVELS

    # ---- findings / coverage -----------------------------------------------------

    @cached_property
    def findings_by_ra(self) -> dict[int, list[FindingRef]]:
        out: dict[int, list[FindingRef]] = defaultdict(list)
        ids = list(self.by_id)
        now = datetime.now(UTC)
        for chunk in _chunked(ids):
            for f, decision in self.session.execute(
                select(Finding, ReviewDecision)
                .outerjoin(ReviewDecision, ReviewDecision.fingerprint == Finding.fingerprint)
                .where(Finding.role_assignment_id.in_(chunk))
            ).all():
                dec = ReviewDecisionRead.model_validate(decision) if decision is not None else None
                out[f.role_assignment_id].append(FindingRef(f, finding_status(dec, now), dec))
        return out

    @cached_property
    def coverage_by_ra(self) -> dict[int, dict[str, Any]]:
        scan_ids = {ra.scan_id for ra in self.ras}
        out: dict[int, dict[str, Any]] = {}
        if not scan_ids:
            return out
        for (payload,) in self.session.execute(
            select(ScanCoverage.payload).where(ScanCoverage.scan_id.in_(scan_ids))
        ).all():
            for row in (payload or {}).get("rows", []):
                aid = row.get("assignment_id")
                if aid in self.by_id:
                    out[aid] = row
        return out

    # ---- per-scope stats -----------------------------------------------------------

    @cached_property
    def scope_stats(self) -> dict[str, dict[str, Any]]:
        """``{scope_id: {direct_assignments, assignments_below, high_risk, open_findings,
        levels_below}}``; ``levels_below`` counts the grants given there or inside
        by capability level (access / keys / change / data / view / unknown)."""
        levels_owned: dict[str, Counter[str]] = defaultdict(Counter)
        direct: Counter[str] = Counter()
        high: Counter[str] = Counter()
        owned: Counter[str] = Counter()
        open_findings: Counter[str] = Counter()
        for ra in self.ras:
            s = ra.scope.lower()
            direct[s] += 1
            if self.risk.get(ra.role_definition_id) == "high":
                high[s] += 1
            owner = self.owner.get(ra.id)
            if owner:
                owned[owner] += 1
                levels_owned[owner][self.capability_of(ra).level] += 1
            open_findings[s] += sum(
                1 for ref in self.findings_by_ra.get(ra.id, ()) if ref.status == "open"
            )

        below: dict[str, int] = {}

        def total(sid: str, depth: int = 0) -> int:
            if sid in below:
                return below[sid]
            if depth > 64:  # cycle guard; registry parents should never loop
                return owned.get(sid, 0)
            below[sid] = owned.get(sid, 0) + sum(
                total(c, depth + 1) for c in self.children.get(sid, ())
            )
            return below[sid]

        levels_below: dict[str, Counter[str]] = {}

        def levels(sid: str, depth: int = 0) -> Counter[str]:
            if sid in levels_below:
                return levels_below[sid]
            acc = Counter(levels_owned.get(sid, Counter()))
            if depth <= 64:
                for c in self.children.get(sid, ()):
                    acc.update(levels(c, depth + 1))
            levels_below[sid] = acc
            return acc

        out: dict[str, dict[str, Any]] = {}
        for sid in self.pointer:
            out[sid] = {
                "direct_assignments": direct.get(sid, 0),
                "assignments_below": total(sid),
                "high_risk": high.get(sid, 0),
                "open_findings": open_findings.get(sid, 0),
                "levels_below": dict(levels(sid)),
            }
        return out


_VIEW_KEY = "current_view"


def current_view(session: Session) -> CurrentView:
    """This session's current view, built on first use. Visibility checks and the
    endpoint itself share it instead of building it twice per request."""
    view = session.info.get(_VIEW_KEY)
    if view is None:
        view = session.info[_VIEW_KEY] = CurrentView(session)
    return view


@event.listens_for(Session, "after_flush")
@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _forget_current_view(session: Session, *_args: Any) -> None:
    """A write, a commit (a long-lived session then sees others' writes) or a
    rollback makes the view stale: the next use builds it again."""
    session.info.pop(_VIEW_KEY, None)
