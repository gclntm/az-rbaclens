import { Link } from "react-router-dom";

import type { AssignmentSource, CurrentFinding, DecisionWrite, FindingStatus } from "@/api/client";
import { BADGE, BADGE_TONE, cx, FILTER_BAR, SEV_DOT, SEV_DOT_TONE } from "@/components/classes";
import DecisionButtons from "@/components/DecisionButtons";
import { RoleLink } from "@/components/RoleDetailDrawer";
import { useMe } from "@/hooks/useMe";
import { principalHref } from "@/pages/principals/principalUtils";
import {
  CATEGORY_LABEL,
  formatDateTime,
  principalLabel,
  scopeHref,
  scopeLabel,
  SOURCE_LABEL,
} from "@/pages/scopes/scopeUtils";

import { hasEvidence, parseEvidence } from "./findingEvidence";

export const STATUS_LABEL: Record<FindingStatus, string> = {
  open: "Open",
  reviewed: "Reviewed",
  accepted_risk: "Accepted risk",
  snoozed: "Snoozed",
  resolved: "Resolved",
};

type CardFinding = CurrentFinding & { source?: AssignmentSource };

const PURPLE = "bg-(--accent-purple-subtle) text-(--accent-purple)";
/** Badge colours: a finding's severity, where its grant comes from, its decision. */
export const SEV_BADGE: Record<string, string> = {
  high: BADGE_TONE.sevHigh,
  medium: BADGE_TONE.sevMedium,
  low: BADGE_TONE.sevLow,
};
export const SOURCE_BADGE: Record<string, string> = {
  direct: BADGE_TONE.running,
  inherited: PURPLE,
  below: BADGE_TONE.sevLow,
};
export const STATUS_BADGE: Record<string, string> = {
  open: BADGE_TONE.running,
  reviewed: BADGE_TONE.done,
  resolved: BADGE_TONE.done,
  accepted_risk: PURPLE,
  snoozed: BADGE_TONE.sevLow,
};

/** The parts of a finding in a list (the list itself, and its <li>, are the page's). */
export const FEED_MAIN = "flex min-w-0 flex-[1_1_240px] flex-col gap-0.5";
export const FEED_TITLE = "font-medium [overflow-wrap:anywhere]";
export const FEED_META = "text-[0.75rem] text-tertiary [overflow-wrap:anywhere]";
export const SUGGEST = "text-[0.8125rem] text-tertiary";
export const SUGGEST_LABEL = "mr-1 text-[0.6875rem] font-semibold tracking-[0.06em] text-success-primary uppercase";
const CODE = "font-mono text-[0.75rem]";

export function Evidence({ finding }: { finding: CardFinding }) {
  const e = parseEvidence(finding.evidence);
  if (!hasEvidence(e)) return null;
  return (
    <details className="text-[0.8125rem]">
      <summary className="w-fit cursor-pointer font-medium text-(--accent-blue)">Evidence</summary>
      <dl
        className={
          "mx-0 mt-2 mb-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-[4px] bg-primary_hover px-4 py-2 " +
          "[&_dd]:m-0 [&_dd]:min-w-0 [&_dd]:[overflow-wrap:anywhere] [&_dt]:font-medium [&_dt]:text-tertiary"
        }
      >
        {e.usage && (
          <>
            <dt>Changes made</dt>
            <dd>
              {e.usage.used} of {e.usage.granted} of the role&apos;s permissions used for changes here (
              {e.usage.coveragePct} %) · {e.usage.observedOps} kind
              {e.usage.observedOps === 1 ? "" : "s"} of change by this identity in the window, anywhere. Reads are never recorded
            </dd>
          </>
        )}
        {e.unusedPatterns.length > 0 && (
          <>
            <dt>Never exercised</dt>
            <dd className="flex flex-wrap gap-x-2 gap-y-1">
              {e.unusedPatterns.map((p) => (
                <code key={p} className={CODE}>
                  {p}
                </code>
              ))}
            </dd>
          </>
        )}
        {e.topOperations.length > 0 && (
          <>
            <dt>Top operations</dt>
            <dd>
              <ul className="m-0 grid list-none gap-0.5 p-0">
                {e.topOperations.map((o) => (
                  <li key={o.name}>
                    <code className={CODE}>{o.name}</code> <span className="text-quaternary">× {o.count}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {e.recommendations.length > 0 && (
          <>
            <dt>Narrower built-ins</dt>
            <dd className="flex flex-wrap gap-x-2 gap-y-1">
              {e.recommendations.map((r) => (
                <span key={r.roleDefinitionId}>
                  <RoleLink roleId={r.roleDefinitionId} roleName={r.roleName} />{" "}
                  <span className="text-quaternary">
                    ({r.patterns} pattern{r.patterns === 1 ? "" : "s"}, {r.saved} fewer)
                  </span>
                </span>
              ))}
            </dd>
          </>
        )}
        {e.coveredBy.length > 0 && (
          <>
            <dt>Covered by</dt>
            <dd>
              {e.coveredBy.map((c, i) => (
                <span key={`${c.scope}-${i}`}>
                  {i > 0 && "; "}
                  {c.roleName ?? "a role"} at{" "}
                  <Link to={scopeHref(c.scope)}>{scopeLabel(c.scope, c.scopeName)}</Link>
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
    </details>
  );
}

/**
 * One finding: what, why, evidence, suggested action and the decision
 * buttons. The grant page lists its findings one by one; lists of many
 * grants use GrantFindingsCard, one card per grant.
 */
export default function FindingCard({
  finding,
  busy,
  onDecide,
  onReopen,
  showPrincipal = false,
}: {
  finding: CardFinding;
  busy: boolean;
  onDecide: (body: DecisionWrite, label: string) => void;
  onReopen: () => void;
  showPrincipal?: boolean;
}) {
  const d = finding.decision;
  const me = useMe();
  // Decisions on findings are for admins; others decide in their monthly review.
  const readOnly = !!me && me.signed_in && !me.is_admin;
  return (
    <li>
      <span className={cx(SEV_DOT, SEV_DOT_TONE[finding.severity] ?? SEV_DOT_TONE.low)} title={`${finding.severity} severity`} />
      <div className={FEED_MAIN}>
        <div className={FILTER_BAR} style={{ gap: "var(--space-xs)" }}>
          <span className={cx(BADGE, SEV_BADGE[finding.severity])}>
            {CATEGORY_LABEL[finding.category] ?? finding.category}
          </span>
          {finding.source && (
            <span className={cx(BADGE, SOURCE_BADGE[finding.source])}>{SOURCE_LABEL[finding.source]}</span>
          )}
          {finding.status !== "open" && (
            <span className={cx(BADGE, STATUS_BADGE[finding.status])}>{STATUS_LABEL[finding.status]}</span>
          )}
        </div>
        <span className={FEED_TITLE}>{finding.title}</span>
        <span className={FEED_META}>{finding.detail}</span>
        {finding.suggestion && (
          <span className={SUGGEST}>
            <span className={SUGGEST_LABEL}>Suggested</span>
            {finding.suggestion}
          </span>
        )}
        <Evidence finding={finding} />
        <span className={FEED_META}>
          {showPrincipal && (
            <>
              <Link to={principalHref(finding.principal_id)}>{principalLabel(finding)}</Link> (
              {finding.principal_type}) ·{" "}
            </>
          )}
          Given on <Link to={scopeHref(finding.scope)}>{scopeLabel(finding.scope, finding.scope_name)}</Link>
          {" · "}check #{finding.scan_id}
          {d && (
            <>
              {" · "}
              {STATUS_LABEL[d.decision]} by {d.actor_name ?? d.actor ?? "unknown"} on{" "}
              {formatDateTime(d.decided_at)}
              {d.decision === "snoozed" && d.snoozed_until && ` until ${formatDateTime(d.snoozed_until)}`}
              {d.note && ` — “${d.note}”`}
            </>
          )}
        </span>
      </div>
      {!readOnly && (
        <DecisionButtons
          open={finding.status === "open"}
          busy={busy}
          onDecide={onDecide}
          onReopen={onReopen}
        />
      )}
    </li>
  );
}
