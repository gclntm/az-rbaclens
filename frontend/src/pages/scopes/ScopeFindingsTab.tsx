import { useMemo, useState } from "react";

import { pillClass } from "@/components/chips";
import {
  api,
  type DecisionWrite,
  type FindingSeverity,
  type FindingStatus,
  type ScopeFinding,
  type ScopeFindings,
} from "@/api/client";
import { STATUS_LABEL } from "@/components/FindingCard";
import { groupByGrant } from "@/components/findingGroups";
import GrantFindingsCard from "@/components/GrantFindingsCard";
import { SectionCard } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { CAPTION, EMPTY_STATE, FILTER_BAR, FILTER_SEP, NUM, SEV_DOT, SEV_DOT_TONE } from "@/components/classes";
import { cx } from "@/components/classes";

import { CATEGORY_LABEL, reviewProgress, SEVERITY_ORDER } from "./scopeUtils";

const SEVERITIES: FindingSeverity[] = ["high", "medium", "low"];
type StatusFilter = "open" | "decided" | "all";

/** One card per grant, with a line between them (the cards are GrantFindingsCard's li). */
const FINDINGS_LIST =
  "m-0 list-none p-0 " +
  "[&>li]:flex [&>li]:flex-wrap [&>li]:items-start [&>li]:gap-4 [&>li]:border-t [&>li]:border-t-(--border) [&>li]:px-6 [&>li]:py-4 " +
  "[&>li:first-child]:[border-top:0] [&_.sev-dot]:mt-[7px]";
/** Names and counts in two columns. */
const CAT_LIST = "grid grid-cols-[minmax(0,1fr)_32px] gap-x-4 gap-y-2 text-[0.8125rem]";
const CAT_BUTTON =
  "flex cursor-pointer items-center gap-2 bg-transparent p-0 text-left text-inherit [border:0] [font:inherit] hover:text-(--accent-blue)";

/** Review worklist for a scope: findings on every assignment effective at or below it. */
export default function ScopeFindingsTab({
  findings,
  onChanged,
}: {
  findings: ScopeFindings;
  onChanged: () => void;
}) {
  const { showToast } = useToast();
  const [severity, setSeverity] = useState<FindingSeverity | "all">("all");
  const [status, setStatus] = useState<StatusFilter>("open");
  const [category, setCategory] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const shown = useMemo(
    () =>
      findings.findings.filter(
        (f) =>
          (severity === "all" || f.severity === severity) &&
          (category === null || f.category === category) &&
          (status === "all" || (status === "open" ? f.status === "open" : f.status !== "open")),
      ),
    [findings.findings, severity, status, category],
  );
  const { summary } = findings;
  const progress = reviewProgress(summary);
  const categories = Object.entries(summary.by_category).sort((a, b) => b[1] - a[1]);
  const severityOf = (cat: string) =>
    findings.findings
      .filter((f) => f.category === cat)
      .reduce<FindingSeverity>((w, f) => (SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[w] ? f.severity : w), "low");

  const groups = useMemo(() => groupByGrant(shown), [shown]);

  async function decide(key: string, fs: ScopeFinding[], body: DecisionWrite, label: string) {
    setBusy(key);
    try {
      await api.findings.decideMany(fs.map((f) => f.fingerprint), body);
      showToast(label, "success");
      onChanged();
    } catch (e) {
      showToast(`Could not save the decision: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  async function reopen(key: string, fs: ScopeFinding[]) {
    setBusy(key);
    try {
      await Promise.all(fs.map((f) => api.findings.reopen(f.fingerprint)));
      showToast("Reopened", "success");
      onChanged();
    } catch (e) {
      showToast(`Could not reopen: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="flex flex-wrap items-start gap-4">
      <div className="min-w-0 flex-[999_1_560px]">
        <SectionCard
          icon="look"
          tone={summary.open > 0 ? "warn" : "ok"}
          title="Grants to look at"
          hint="On every grant that applies here or inside, one card per grant with all its reasons."
        >
          <div className={FILTER_BAR} style={{ marginBottom: "var(--space-md)" }}>
            {(["open", "decided", "all"] as StatusFilter[]).map((s) => (
              <button
                key={s}
                type="button"
                className={pillClass(status === s)}
                aria-pressed={status === s}
                onClick={() => setStatus(s)}
              >
                {s === "open" ? `Open ${summary.open}` : s === "decided" ? `Decided ${summary.total - summary.open}` : `All ${summary.total}`}
              </button>
            ))}
            <span className={FILTER_SEP} aria-hidden="true" />
            <button
              type="button"
              className={pillClass(severity === "all")}
              aria-pressed={severity === "all"}
              onClick={() => setSeverity("all")}
            >
              Any severity
            </button>
            {SEVERITIES.map((s) => (
              <button
                key={s}
                type="button"
                className={pillClass(severity === s)}
                aria-pressed={severity === s}
                onClick={() => setSeverity(s)}
              >
                <span className={cx(SEV_DOT, SEV_DOT_TONE[s])} aria-hidden="true" />
                {s[0].toUpperCase() + s.slice(1)} <span className="text-quaternary">{summary.by_severity[s] ?? 0}</span>
              </button>
            ))}
            {category && (
              <button type="button" className={pillClass(true)} onClick={() => setCategory(null)}>
                {CATEGORY_LABEL[category] ?? category} ✕
              </button>
            )}
          </div>

          <div>
            {summary.total === 0 ? (
              <p className={cx("my-[1em]", EMPTY_STATE)}>
                Nothing needs a look here. This list is worked out after each daily check.
              </p>
            ) : shown.length === 0 ? (
              <p className={cx("my-[1em]", EMPTY_STATE)}>Nothing matches these filters.</p>
            ) : (
              <ul className={FINDINGS_LIST}>
                {groups.map((g) => (
                  <GrantFindingsCard
                    key={g.key}
                    group={g}
                    busy={busy === g.key}
                    showPrincipal
                    onDecide={(fs, body, label) => decide(g.key, fs, body, label)}
                    onReopen={(fs) => reopen(g.key, fs)}
                  />
                ))}
              </ul>
            )}
          </div>
        </SectionCard>
      </div>

      <aside className="flex max-w-[340px] flex-[1_1_280px] flex-col gap-4 max-[720px]:max-w-none">
        <SectionCard icon="reviews" title="Decided" hint={`${progress.done} of ${progress.total} reasons (${progress.pct} %).`}>
          <div className="h-2 w-full overflow-hidden rounded-[2px] bg-primary_hover" aria-label={`${progress.pct} % decided`}>
            <div className="h-full rounded-[2px] bg-(--accent-blue) [transition:width_200ms_ease-out]" style={{ width: `${progress.pct}%` }} />
          </div>
          <div className={CAT_LIST} style={{ marginTop: "var(--space-md)" }}>
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

        {categories.length > 0 && (
          <SectionCard icon="look" title="By category" hint="Pick one to show only it.">
            <div className={CAT_LIST}>
              {categories.map(([cat, n]) => (
                <span key={cat} style={{ display: "contents" }}>
                  <button
                    type="button"
                    className={cx("tracking-normal", CAT_BUTTON, category === cat && "text-(--accent-blue)")}
                    aria-pressed={category === cat}
                    onClick={() => setCategory(category === cat ? null : cat)}
                  >
                    <span className={cx(SEV_DOT, SEV_DOT_TONE[severityOf(cat)])} aria-hidden="true" />
                    {CATEGORY_LABEL[cat] ?? cat}
                  </button>
                  <span className={NUM}>{n}</span>
                </span>
              ))}
            </div>
          </SectionCard>
        )}

        <SectionCard icon="history" title="How this list is worked out">
          <div className={cx(CAPTION, "text-secondary")} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span>
              <strong>Changes made</strong>: what the Activity Log recorded in 90 days, matched against
              what the role allows, for the identity and, for a group, its members. Reads are never recorded.
            </span>
            <span>
              <strong>Already covered</strong>: the same identity holds an equal or stronger role on a
              place above.
            </span>
            <span>
              A decision stays with the grant, so a reviewed item stays reviewed after the next daily
              check.
            </span>
          </div>
        </SectionCard>
      </aside>
    </section>
  );
}
