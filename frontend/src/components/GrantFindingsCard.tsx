import { Link } from "react-router-dom";

import type { AssignmentSource, CurrentFinding, DecisionWrite } from "@/api/client";
import { BADGE, cx, FILTER_BAR, SEV_DOT, SEV_DOT_TONE } from "@/components/classes";
import DecisionButtons from "@/components/DecisionButtons";
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

import {
  Evidence,
  FEED_MAIN,
  FEED_META,
  FEED_TITLE,
  SEV_BADGE,
  SOURCE_BADGE,
  STATUS_BADGE,
  STATUS_LABEL,
  SUGGEST,
  SUGGEST_LABEL,
} from "./FindingCard";
import type { FindingGroup } from "./findingGroups";

/**
 * One grant with every reason the checks flagged it for: each reason with its detail, suggestion
 * and evidence, then one set of buttons that decides all of its open reasons at once.
 */
export default function GrantFindingsCard<F extends CurrentFinding>({
  group,
  busy,
  onDecide,
  onReopen,
  showPrincipal = false,
  selected,
  onToggleSelected,
}: {
  group: FindingGroup<F>;
  busy: boolean;
  /** For deciding many grants at once (the Needs a look page). */
  selected?: boolean;
  onToggleSelected?: () => void;
  /** Decide these findings (the group's open ones) together. */
  onDecide: (findings: F[], body: DecisionWrite, label: string) => void;
  onReopen: (findings: F[]) => void;
  showPrincipal?: boolean;
}) {
  const me = useMe();
  const readOnly = !!me && me.signed_in && !me.is_admin;
  const first = group.findings[0];
  // Only the place page's findings say whether the grant is direct or inherited from above.
  const source = (first as CurrentFinding & { source?: AssignmentSource }).source;
  const many = group.findings.length > 1;
  const open = group.open;
  return (
    <li>
      {onToggleSelected ? (
        <input
          type="checkbox"
          className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mr-[3px] mb-[3px] ml-1 mt-1 size-4 flex-[0_0_16px]"
          checked={!!selected}
          onChange={onToggleSelected}
          aria-label={`Select: ${first.title}`}
        />
      ) : (
        <span className={cx(SEV_DOT, SEV_DOT_TONE[group.severity] ?? SEV_DOT_TONE.low)} title={`${group.severity} severity`} />
      )}
      <div className={FEED_MAIN}>
        <span className={FEED_TITLE}>
          {first.title}
          {source && <span className={cx(BADGE, SOURCE_BADGE[source])}> {SOURCE_LABEL[source]}</span>}
        </span>
        <ul className="mx-0 mt-1 mb-1.5 flex list-none flex-col gap-2 p-0">
          {group.findings.map((f) => (
            <li key={f.fingerprint} className={cx(
                "flex flex-col gap-0.5 border-l-2 border-l-secondary pl-2",
                f.status !== "open" && "opacity-75",
              )}>
              <div className={FILTER_BAR} style={{ gap: "var(--space-xs)" }}>
                <span className={cx(BADGE, SEV_BADGE[f.severity])}>{CATEGORY_LABEL[f.category] ?? f.category}</span>
                {f.status !== "open" && (
                  <span className={cx(BADGE, STATUS_BADGE[f.status])}>{STATUS_LABEL[f.status]}</span>
                )}
              </div>
              <span className={FEED_META}>{f.detail}</span>
              {f.suggestion && (
                <span className={SUGGEST}>
                  <span className={SUGGEST_LABEL}>Suggested</span>
                  {f.suggestion}
                </span>
              )}
              <Evidence finding={f} />
              {f.decision && (
                <span className={FEED_META}>
                  {STATUS_LABEL[f.decision.decision]} by {f.decision.actor_name ?? f.decision.actor ?? "unknown"} on{" "}
                  {formatDateTime(f.decision.decided_at)}
                  {f.decision.decision === "snoozed" &&
                    f.decision.snoozed_until &&
                    ` until ${formatDateTime(f.decision.snoozed_until)}`}
                  {f.decision.note && ` — “${f.decision.note}”`}
                </span>
              )}
            </li>
          ))}
        </ul>
        <span className={FEED_META}>
          {showPrincipal && (
            <>
              <Link to={principalHref(first.principal_id)}>{principalLabel(first)}</Link> ({first.principal_type}) ·{" "}
            </>
          )}
          Given on <Link to={scopeHref(first.scope)}>{scopeLabel(first.scope, first.scope_name)}</Link>
          {" · "}check #{first.scan_id}
          {many && ` · ${group.findings.length} reasons`}
        </span>
      </div>
      {!readOnly && (
        <DecisionButtons
          open={open.length > 0}
          busy={busy}
          what={open.length > 1 ? `${open.length} reasons` : undefined}
          onDecide={(body, label) => onDecide(open, body, label)}
          onReopen={() => onReopen(group.findings)}
        />
      )}
    </li>
  );
}
