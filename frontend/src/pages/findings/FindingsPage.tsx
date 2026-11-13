import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { pillClass } from "@/components/chips";
import {
  EMPTY_STATE,
  FILTER_BAR,
  FILTER_SEP,
  NUM,
  SEV_DOT,
  SEV_DOT_TONE,
  TABLE_FOOT,
} from "@/components/classes";
import Button from "@/components/Button";
import {
  api,
  type CurrentFinding,
  type CurrentFindings,
  type DecisionWrite,
  type FindingListParams,
  type FindingSeverity,
  type FindingStatus,
} from "@/api/client";
import DecisionButtons from "@/components/DecisionButtons";
import { STATUS_LABEL } from "@/components/FindingCard";
import { groupByGrant } from "@/components/findingGroups";
import GrantFindingsCard from "@/components/GrantFindingsCard";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { CATEGORY_LABEL, reviewProgress } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

/** Grants per page: each comes with all its reasons. */
const PAGE = 30;
const SEVERITIES: FindingSeverity[] = ["high", "medium", "low"];
type StatusFilter = "open" | "decided" | "all";

/**
 * A list of grant cards (GrantFindingsCard renders each <li>): rows with a line
 * between them, a little more room on the sides, the severity dot level with the title.
 */
const FINDINGS_LIST =
  "m-0 list-none p-0 " +
  "[&>li]:flex [&>li]:flex-wrap [&>li]:items-start [&>li]:gap-4 [&>li]:border-t [&>li]:border-t-secondary [&>li]:px-6 [&>li]:py-4 [&>li:first-child]:border-t-0 " +
  "[&_.sev-dot]:mt-[7px]";
/** Name / count pairs in two columns (the side rail). */
const CAT_LIST = "grid grid-cols-[minmax(0,1fr)_32px] gap-x-4 gap-y-2 text-[0.8125rem]";
const CAT_BUTTON =
  "flex cursor-pointer items-center gap-2 [border:0] bg-transparent p-0 text-left [font:inherit] hover:text-(--accent-blue)";

function readFilters(sp: URLSearchParams): Required<Pick<FindingListParams, "q">> & {
  status: StatusFilter;
  severity: FindingSeverity | null;
  category: string | null;
} {
  const status = sp.get("status");
  const severity = sp.get("severity");
  return {
    status: status === "decided" || status === "all" ? status : "open",
    severity: SEVERITIES.includes(severity as FindingSeverity) ? (severity as FindingSeverity) : null,
    category: sp.get("category"),
    q: sp.get("q") ?? "",
  };
}

/**
 * Tenant-wide review worklist: every grant with something to look at, worst
 * first, one card per grant with all its reasons (as on a place or a person),
 * with decisions on many grants at once. Counts are of reasons, like
 * everywhere else. Filters live in the URL so other pages can deep-link
 * (e.g. /findings?category=unused).
 */
export default function FindingsPage() {
  const { showToast } = useToast();
  const [sp, setSp] = useSearchParams();
  const filters = readFilters(sp);
  const [query, setQuery] = useState(filters.q);
  const [data, setData] = useState<CurrentFindings | null>(null);
  const [rows, setRows] = useState<CurrentFinding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  /** Selected grants, by group key. */
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);

  const params = useMemo<FindingListParams>(
    () => ({
      status: filters.status === "all" ? undefined : filters.status,
      severity: filters.severity ?? undefined,
      category: filters.category ?? undefined,
      q: filters.q || undefined,
      limit: PAGE,
      per: "grant",
    }),
    [filters.status, filters.severity, filters.category, filters.q],
  );

  const load = useCallback(
    (offset = 0) => {
      setLoading(true);
      api.findings
        .list({ ...params, offset })
        .then((d) => {
          setData(d);
          setRows((prev) => (offset === 0 ? d.findings : [...prev, ...d.findings]));
          setError(null);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => setLoading(false));
    },
    [params],
  );

  useEffect(() => {
    setSelected(new Set());
    load(0);
  }, [load]);

  // Debounce the search box into the URL.
  useEffect(() => {
    const t = setTimeout(() => setFilter("q", query.trim() || null), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the typed text drives this
  }, [query]);

  function setFilter(key: string, value: string | null) {
    const next = new URLSearchParams(sp);
    if (value === null || value === "" || (key === "status" && value === "open")) next.delete(key);
    else next.set(key, value);
    if (next.toString() !== sp.toString()) setSp(next, { replace: true });
  }

  const groups = useMemo(() => groupByGrant(rows), [rows]);
  const loadedGrants = groups.length;

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const allSelected = groups.length > 0 && groups.every((g) => selected.has(g.key));
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(groups.map((g) => g.key)));
  }
  // Bulk decisions decide what is still open on the selected grants.
  const selectedOpen = groups.filter((g) => selected.has(g.key)).flatMap((g) => g.open);

  async function save(key: string, action: () => Promise<unknown>, label: string, after?: () => void) {
    setBusy(key);
    try {
      await action();
      showToast(label, "success");
      after?.();
      load(0);
    } catch (e) {
      showToast(`Could not save: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  function decide(key: string, fs: CurrentFinding[], body: DecisionWrite, label: string, after?: () => void) {
    return save(key, () => api.findings.decideMany(fs.map((f) => f.fingerprint), body), label, after);
  }

  function reopen(key: string, fs: CurrentFinding[]) {
    return save(key, () => Promise.all(fs.map((f) => api.findings.reopen(f.fingerprint))), "Reopened");
  }

  const summary = data?.summary;
  const progress = summary ? reviewProgress(summary) : null;
  const categories = summary ? Object.entries(summary.by_category).sort((a, b) => b[1] - a[1]) : [];

  const openGrants = summary?.open_grants ?? 0;
  return (
    <>
      <PageHeader
        title="Needs a look"
        sub={
          <>
            Every grant with something to look at, most important first, one card per grant with all its reasons.
            &quot;No change in the window&quot; is evidence, not a problem: Azure never records reads.
          </>
        }
      />

      {summary && progress && (
        <StatusTiles>
          <StatusTile
            tone={openGrants > 0 ? "warn" : "ok"}
            icon="look"
            label="Open"
            value={openGrants > 0 ? `${openGrants.toLocaleString()} ${openGrants === 1 ? "grant" : "grants"}` : "Nothing"}
            note={`${summary.open.toLocaleString()} open ${summary.open === 1 ? "reason" : "reasons"}`}
          />
          <StatusTile
            tone={(summary.by_severity.high ?? 0) > 0 ? "danger" : "ok"}
            icon="shield"
            label="High priority"
            value={`${(summary.by_severity.high ?? 0).toLocaleString()} ${summary.by_severity.high === 1 ? "reason" : "reasons"}`}
            note="Deleted identities, powerful roles given high up"
          />
          <StatusTile
            tone={progress.total > 0 && progress.done === progress.total ? "ok" : "info"}
            icon="reviews"
            label="Decided"
            value={`${progress.pct} %`}
            note={`${progress.done.toLocaleString()} of ${progress.total.toLocaleString()} reasons`}
          />
        </StatusTiles>
      )}

      <section className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-[999_1_560px]">
          <SectionCard
            icon="look"
            tone={openGrants > 0 ? "warn" : "ok"}
            title="Grants to look at"
            hint="Decide one card, or select several and decide them together."
          >
          <div className={FILTER_BAR}>
            <input
              type="search"
              className="font-normal tracking-normal max-w-[360px] flex-[1_1_240px]"
              placeholder="Find a person, role or place"
              aria-label="Find a person, role or place"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {(["open", "decided", "all"] as StatusFilter[]).map((s) => (
              <button
                key={s}
                type="button"
                className={pillClass(filters.status === s)}
                aria-pressed={filters.status === s}
                onClick={() => setFilter("status", s)}
              >
                {s === "open" ? "Open" : s === "decided" ? "Decided" : "All"}
                {summary && (
                  <span className="text-quaternary">
                    {s === "open" ? summary.open : s === "decided" ? summary.total - summary.open : summary.total}
                  </span>
                )}
              </button>
            ))}
            <span className={FILTER_SEP} aria-hidden="true" />
            {SEVERITIES.map((s) => (
              <button
                key={s}
                type="button"
                className={pillClass(filters.severity === s)}
                aria-pressed={filters.severity === s}
                onClick={() => setFilter("severity", filters.severity === s ? null : s)}
              >
                <span className={cx(SEV_DOT, SEV_DOT_TONE[s])} aria-hidden="true" />
                {s[0].toUpperCase() + s.slice(1)}
                {summary && <span className="text-quaternary">{summary.by_severity[s] ?? 0}</span>}
              </button>
            ))}
            {filters.category && (
              <button type="button" className={pillClass(true)} onClick={() => setFilter("category", null)}>
                {CATEGORY_LABEL[filters.category] ?? filters.category} ✕
              </button>
            )}
          </div>
              <div className="sticky top-0 z-5 mx-0 mt-0 mb-2 flex flex-wrap items-center gap-2 border-b border-b-secondary bg-primary px-0 py-2">
                <label className={pillClass(false)} style={{ border: 0 }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={groups.length === 0} />
                  {selected.size > 0
                    ? `${selected.size} ${selected.size === 1 ? "grant" : "grants"} selected`
                    : "Select all loaded"}
                </label>
                <span className={FILTER_SEP} aria-hidden="true" />
                <DecisionButtons
                  open
                  busy={busy === "bulk"}
                  disabled={busy !== null || selectedOpen.length === 0}
                  what={`${selectedOpen.length} ${selectedOpen.length === 1 ? "reason" : "reasons"} on ${selected.size} ${selected.size === 1 ? "grant" : "grants"}`}
                  onDecide={(body, label) => decide("bulk", selectedOpen, body, label, () => setSelected(new Set()))}
                />
              </div>
              {error && <p className="my-[1em] text-error-primary">Could not load the list: {error}</p>}
              {!error && data && rows.length === 0 && (
                <p className={cx("my-[1em]", EMPTY_STATE)}>
                  {summary?.total === 0
                    ? "Nothing yet. This list is worked out after each daily check."
                    : "Nothing matches these filters."}
                </p>
              )}
              {groups.length > 0 && (
                <ul className={FINDINGS_LIST}>
                  {groups.map((g) => (
                    <GrantFindingsCard
                      key={g.key}
                      group={g}
                      busy={busy === g.key}
                      showPrincipal
                      selected={selected.has(g.key)}
                      onToggleSelected={() => toggle(g.key)}
                      onDecide={(fs, body, label) => decide(g.key, fs, body, label)}
                      onReopen={(fs) => reopen(g.key, fs)}
                    />
                  ))}
                </ul>
              )}
              {data && (
                <div className={TABLE_FOOT}>
                  <span>
                    Showing {loadedGrants} of {data.total} {data.total === 1 ? "grant" : "grants"}
                  </span>
                  {loadedGrants < data.total && (
                    <Button
                      size="sm"
                      type="button"
                      disabled={loading}
                      onClick={() => load(loadedGrants)}
                    >
                      {loading ? "Loading…" : `Load ${Math.min(PAGE, data.total - loadedGrants)} more`}
                    </Button>
                  )}
                </div>
              )}
          </SectionCard>
        </div>

        <aside className="flex max-w-[340px] flex-[1_1_280px] flex-col gap-4 max-[720px]:max-w-none">
          {summary && (
            <SectionCard icon="reviews" title="By state">
              <div className={CAT_LIST}>
                {(Object.keys(STATUS_LABEL) as FindingStatus[])
                  .filter((s) => summary.by_status[s])
                  .map((s) => (
                    <span key={s} style={{ display: "contents" }}>
                      <span>{STATUS_LABEL[s]}</span>
                      <span className={NUM}>{summary.by_status[s]}</span>
                    </span>
                  ))}
              </div>
            </SectionCard>
          )}
          {categories.length > 0 && (
            <SectionCard icon="look" title="By category" hint="Pick one to show only it.">
              <div className={CAT_LIST}>
                {categories.map(([cat, n]) => (
                  <span key={cat} style={{ display: "contents" }}>
                    <button
                      type="button"
                      className={cx("tracking-normal", CAT_BUTTON, filters.category === cat ? "text-(--accent-blue)" : "text-inherit")}
                      aria-pressed={filters.category === cat}
                      onClick={() => setFilter("category", filters.category === cat ? null : cat)}
                    >
                      {CATEGORY_LABEL[cat] ?? cat}
                    </button>
                    <span className={NUM}>{n}</span>
                  </span>
                ))}
              </div>
            </SectionCard>
          )}
        </aside>
      </section>
    </>
  );
}
