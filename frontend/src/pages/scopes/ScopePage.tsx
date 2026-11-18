import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { copyButtonClass } from "@/components/chips";
import Button from "@/components/Button";
import {
  api,
  isEntraPlace,
  type ResponsibleOf,
  type ScopeAssignments,
  type ScopeDetail,
  type ScopeFindings,
} from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import { Breadcrumbs, PageHeader, StatusTile, StatusTiles, type Tone } from "@/components/PageTemplate";
import { TabCount, TabNav, tabClass } from "@/components/TabNav";
import { useToast } from "@/components/Toast";
import { BADGE, BADGE_LEVEL, BADGE_TONE, CARD, MONO } from "@/components/classes";
import { cx } from "@/components/classes";
import { isRestricted, useMe } from "@/hooks/useMe";

import ScopeAccessTab from "./ScopeAccessTab";
import ScopeActivityTab from "./ScopeActivityTab";
import ScopeFindingsTab from "./ScopeFindingsTab";
import ScopeHistoryTab from "./ScopeHistoryTab";
import ScopeWhoTab from "./ScopeWhoTab";
import {
  ageLabel,
  formatDateTime,
  freshnessOf,
  LEVEL_LABEL,
  parseTab,
  scopeHref,
  scopeIdFromSplat,
  scopeLabel,
  type ScopeTab,
} from "./scopeUtils";

const FRESH_TONE: Record<ReturnType<typeof freshnessOf>, Tone> = {
  fresh: "ok",
  aging: "warn",
  stale: "danger",
  never: "info",
};

const TAB_LABEL: Record<ScopeTab, string> = {
  who: "Who can do what",
  findings: "Needs a look",
  history: "Changes",
  activity: "Activity",
  access: "All grants",
};

/**
 * One place, current state. Route: /places/<ARM path>?tab=who|findings|history|activity|access
 * ("who" is the default; "access" is the full grants table).
 *
 * Loads the detail, assignments and findings together so the header
 * KPIs and tab counts agree; History loads on demand.
 */
export default function ScopePage() {
  const params = useParams();
  const scopeId = scopeIdFromSplat(params["*"]);
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const { showToast } = useToast();

  const [detail, setDetail] = useState<ScopeDetail | null>(null);
  const [assignments, setAssignments] = useState<ScopeAssignments | null>(null);
  const [findings, setFindings] = useState<ScopeFindings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [responsible, setResponsible] = useState<ResponsibleOf | null>(null);
  const me = useMe();

  useEffect(() => {
    setResponsible(null);
    if (!scopeId) return;
    api.responsible
      .of(scopeId)
      .then(setResponsible)
      .catch(() => setResponsible(null));
  }, [scopeId]);

  const load = useCallback(() => {
    if (!scopeId) return;
    Promise.all([
      api.scopes.detail(scopeId),
      api.scopes.assignments(scopeId),
      api.scopes.findings(scopeId),
    ])
      .then(([d, a, f]) => {
        setDetail(d);
        setAssignments(a);
        setFindings(f);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [scopeId]);

  useEffect(() => {
    setDetail(null);
    setAssignments(null);
    setFindings(null);
    load();
  }, [load]);

  const reloadFindings = useCallback(() => {
    if (!scopeId) return;
    Promise.all([api.scopes.findings(scopeId), api.scopes.assignments(scopeId)])
      .then(([f, a]) => {
        setFindings(f);
        setAssignments(a);
      })
      .catch((e: Error) => showToast(`Refresh failed: ${e.message}`, "error"));
  }, [scopeId, showToast]);

  async function refreshSnapshot() {
    if (!detail) return;
    setStarting(true);
    try {
      const scan = await api.iam.startScan(detail.scope.id);
      showToast(`Checking ${scopeLabel(detail.scope.id, detail.scope.name)} now (check #${scan.id})`, "success");
    } catch (e) {
      showToast(`Could not start the check: ${(e as Error).message}`, "error");
    } finally {
      setStarting(false);
    }
  }

  function copyId() {
    if (!detail) return;
    navigator.clipboard
      ?.writeText(detail.scope.id)
      .then(() => showToast("Azure id copied", "success"))
      .catch(() => showToast("Could not copy", "error"));
  }

  function selectTab(next: ScopeTab) {
    const sp = new URLSearchParams(searchParams);
    if (next === "who") sp.delete("tab");
    else sp.set("tab", next);
    setSearchParams(sp, { replace: true });
  }

  if (!scopeId) {
    return (
      <div className={CARD}>
        <p className="my-[1em]">
          No place selected. <Link to="/places">Browse places</Link>.
        </p>
      </div>
    );
  }
  if (error && !detail) {
    return (
      <div className={CARD}>
        <p className="my-[1em] text-error-primary">{error.startsWith("404") ? "This place is not in any daily check yet." : `Could not load this place: ${error}`}</p>
        <Link to="/places">← All places</Link>
      </div>
    );
  }
  if (!detail || !assignments || !findings) {
    return (
      <div className={CARD}>
        <p className="my-[1em] text-quaternary">Loading place…</p>
      </div>
    );
  }

  const { scope } = detail;
  const entra = isEntraPlace(scope.id);
  const latest = scope.latest_scan;
  const fresh = freshnessOf(latest?.age_days);
  const effective = assignments.counts.direct + assignments.counts.inherited;

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Places", to: "/places" },
          ...detail.ancestors.map((a) => ({ label: scopeLabel(a.id, a.name), to: scopeHref(a.id) })),
          { label: scopeLabel(scope.id, scope.name) },
        ]}
      />

      <PageHeader
        title={scopeLabel(scope.id, scope.name)}
        badges={
          <>
            <span className={cx(BADGE, BADGE_LEVEL[scope.level])}>{LEVEL_LABEL[scope.level]}</span>
            {me?.oid && responsible?.responsible?.person_id === me.oid && <span className={cx(BADGE, BADGE_TONE.yours)}>yours</span>}
          </>
        }
        actions={
          !isRestricted(me) && (
            <>
              {latest && <DownloadLink href={api.iam.exportUrl(latest.id, "csv")}>Export CSV</DownloadLink>}
              {/* Entra roles are read by the check of the Tenant Root Group, not on their own. */}
              {!entra && (
                <Button variant="primary" size="sm" type="button" onClick={refreshSnapshot} disabled={starting}>
                  {starting ? "Starting…" : "Check this place now"}
                </Button>
              )}
            </>
          )
        }
      >
        {responsible && <ResponsibleLine data={responsible} admin={!!me?.is_admin} />}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[0.8125rem] text-tertiary">
          <span className={cx(MONO, "[overflow-wrap:anywhere]")}>{scope.id}</span>
          <button type="button" className={copyButtonClass()} onClick={copyId} aria-label="Copy the Azure id">
            Copy
          </button>
        </div>
      </PageHeader>

      <StatusTiles>
        <StatusTile
          tone={FRESH_TONE[fresh]}
          icon="calendar"
          label="Access as of"
          value={latest ? ageLabel(latest.age_days) : "Not checked"}
          note={
            latest
              ? formatDateTime(latest.finished_at)
              : "Known only because access given here applies further down"
          }
        />
        <StatusTile
          tone="info"
          icon="shield"
          label="Grants that apply here"
          value={effective.toLocaleString()}
          note={`${assignments.counts.direct} given here, ${assignments.counts.inherited} from above, ${assignments.counts.below} inside`}
        />
        <StatusTile
          tone={findings.summary.open > 0 ? "warn" : "ok"}
          icon="look"
          label="Needs a look"
          value={findings.summary.open > 0 ? `${findings.summary.open} open` : "Nothing open"}
          note={`${findings.summary.total} reasons in all, here and inside`}
        />
      </StatusTiles>

      <TabNav label="Place sections">
        {/* No Activity tab on Entra ID: the Activity Log doesn't record Entra actions. */}
        {(Object.keys(TAB_LABEL) as ScopeTab[]).filter((t) => !(entra && t === "activity")).map((t) => (
          <a
            key={t}
            href={scopeHref(scope.id, t)}
            className={tabClass(tab === t)}
            aria-current={tab === t ? "page" : undefined}
            onClick={(e) => {
              e.preventDefault();
              selectTab(t);
            }}
          >
            {TAB_LABEL[t]}
            {t === "access" && <TabCount n={effective + assignments.counts.below} />}
            {t === "findings" && findings.summary.open > 0 && <TabCount n={findings.summary.open} problem />}
          </a>
        ))}
      </TabNav>

      {tab === "access" && (
        <ScopeAccessTab scope={scope} assignments={assignments} />
      )}
      {tab === "findings" && (
        <ScopeFindingsTab findings={findings} onChanged={reloadFindings} />
      )}
      {tab === "who" && <ScopeWhoTab scope={scope} />}
      {tab === "activity" && <ScopeActivityTab scopeId={scope.id} />}
      {tab === "history" && <ScopeHistoryTab scopeId={scope.id} />}
    </>
  );
}

/** "Responsible: Marc Dubois (from mg-prod)" or "Nobody is responsible…", under the place title. */
function ResponsibleLine({ data, admin }: { data: ResponsibleOf; admin: boolean }) {
  const settings = `/settings/responsible?place=${encodeURIComponent(data.scope_id)}`;
  const r = data.responsible;
  if (!r) {
    return (
      <div className="mt-1 text-[0.875rem] text-quaternary">
        Nobody is responsible for this place, so its review goes to the admins.
        {admin && (
          <>
            {" "}
            <Link to={settings}>Set someone</Link>
          </>
        )}
      </div>
    );
  }
  const inherited = data.from_scope_id && data.from_scope_id !== data.scope_id;
  return (
    <div className="mt-1 text-[0.875rem]">
      Responsible: <strong title={r.person_email ?? undefined}>{r.person_name ?? r.person_email}</strong>
      {inherited && <span className="text-quaternary"> (from {data.from_scope_name ?? data.from_scope_id})</span>}
      {admin && (
        <>
          {" · "}
          <Link to={settings}>{inherited ? "Set someone here" : "Change"}</Link>
        </>
      )}
    </div>
  );
}
