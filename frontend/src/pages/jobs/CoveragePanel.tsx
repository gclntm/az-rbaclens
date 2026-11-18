import { useState } from "react";
import { Link } from "react-router-dom";

import { linkButtonClass } from "@/components/chips";
import { CAPTION, EMPTY_STATE, cx } from "@/components/classes";
import type { Coverage } from "@/api/client";
import { whenText } from "@/components/FreshnessLine";
import { SectionCard, StatePill } from "@/components/PageTemplate";

import { coverageBar, coveragePill, coverageText, coverageTone, entraAuditText, secondPassText } from "./coverageUtils";

// Subscriptions that are fine are folded away beyond this many.
const OK_SHOWN = 3;

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** One part of a coverage bar; kept in colour on paper. */
const BAR_PART = "block h-full print:[print-color-adjust:exact]";
/** What is still missing, by how urgent it is. */
const MISSING_TONE: Record<string, string> = { warn: "bg-(--accent-orange)", danger: "bg-(--accent-red)" };

/**
 * Activity Log coverage: one bar per subscription, and one for the management groups' logs
 * (collected together), over the days Azure keeps. Green is
 * collected; amber or red is what is still missing at the recent end, with the days left
 * before Azure deletes it. The header says what last night's second pass found.
 */
export default function CoveragePanel({
  coverage,
  retentionDays,
  onCheckEntraAudit,
}: {
  coverage: Coverage | null;
  retentionDays: number | null;
  /** Try the Entra ID audit log now; absent, no button. */
  onCheckEntraAudit?: () => Promise<unknown>;
}) {
  const [all, setAll] = useState(false);
  const [checking, setChecking] = useState(false);
  const entra = coverage ? entraAuditText(coverage.entra_audit, whenText) : null;
  const rows = coverage?.subscriptions ?? [];
  const attention = rows.filter((c) => c.state !== "ok");
  const fine = rows.filter((c) => c.state === "ok");
  const shown = all ? rows : [...attention, ...fine.slice(0, OK_SHOWN)];
  const hidden = rows.length - shown.length;
  const keeps = coverage?.azure_keeps_days ?? 90;
  // The block's band takes the colour of its worst row; blue when all is well.
  const tone = attention.some((c) => coverageTone(c) === "danger") ? "danger" : attention.length > 0 ? "warn" : "info";
  return (
    <SectionCard
      id="coverage"
      icon="changes"
      tone={tone}
      title="Activity Log coverage"
      hint={`Each bar is the ${keeps} days Azure keeps. Green is collected; the rest still has to be, before Azure deletes it.`}
      actions={coverage && <span className={CAPTION_MUTED}>{secondPassText(coverage, whenText)}</span>}
    >
      {coverage === null ? (
        <p className="my-[1em] text-quaternary">Loading…</p>
      ) : rows.length === 0 ? (
        <p className={cx("my-[1em]", EMPTY_STATE)}>No subscription yet. Collection is set up after the first check.</p>
      ) : (
        <div role="list" aria-label="Activity Log coverage by subscription, and for the management groups">
          {shown.map((c) => {
            const bar = coverageBar(c, keeps);
            const tone = coverageTone(c);
            return (
              <div key={c.subscription_id} className="grid grid-cols-[minmax(110px,190px)_minmax(80px,1fr)_minmax(120px,190px)_auto] items-center gap-x-4 gap-y-2 border-t border-t-secondary py-2 max-[700px]:grid-cols-[minmax(0,1fr)_auto]" role="listitem">
                <span className="[overflow-wrap:anywhere]" title={c.subscription_id}>
                  {c.name}
                </span>
                <span
                  className="flex h-2 overflow-hidden rounded-[4px] bg-primary_hover max-[700px]:order-3 max-[700px]:col-[1/-1]"
                  role="img"
                  aria-label={`${bar.collected}% of the last ${keeps} days collected`}
                >
                  <span className={cx(BAR_PART, "bg-(--accent-green)")} style={{ width: `${bar.collected}%` }} />
                  <span className={cx(BAR_PART, MISSING_TONE[tone])} style={{ width: `${bar.missing}%` }} />
                </span>
                <span className={CAPTION_MUTED}>{coverageText(c)}</span>
                <StatePill tone={tone}>{coveragePill(c)}</StatePill>
              </div>
            );
          })}
        </div>
      )}
      <p className={cx("my-[1em]", CAPTION_MUTED, "border-t border-t-secondary pt-2")}>
        {hidden > 0 && (
          <>
            <button type="button" className={linkButtonClass(true)} onClick={() => setAll(true)}>
              {hidden} more, all up to date
            </button>
            {" · "}
          </>
        )}
        Azure never records reads or data access.
        {retentionDays !== null && (
          <>
            {retentionDays ? ` RBAC Lens keeps what it collects for ${retentionDays} days` : " RBAC Lens keeps all it collects"}
            {" ("}
            <Link to="/settings/data-kept">Data kept</Link>
            {")."}
          </>
        )}
      </p>
      {coverage && entra && (
        <p className={cx("my-[1em] flex flex-wrap items-center gap-x-3 gap-y-1", CAPTION_MUTED)}>
          <span className="font-semibold text-secondary">Entra ID audit log</span>
          <StatePill tone={entra.tone}>{coverage.entra_audit.status === "readable" ? "read" : "not read"}</StatePill>
          <span>{entra.text}</span>
          {coverage.entra_audit.status !== "readable" && onCheckEntraAudit && (
            <button
              type="button"
              className={linkButtonClass(true)}
              disabled={checking}
              onClick={() => {
                setChecking(true);
                void onCheckEntraAudit().finally(() => setChecking(false));
              }}
            >
              {checking ? "Checking…" : "Check now"}
            </button>
          )}
        </p>
      )}
    </SectionCard>
  );
}
