import { useState } from "react";

import { pillClass } from "@/components/chips";
import { api, type CurrentFinding, type DecisionWrite } from "@/api/client";
import { CAPTION, EMPTY_STATE, FILTER_BAR } from "@/components/classes";
import { groupByGrant, groupCountText } from "@/components/findingGroups";
import GrantFindingsCard from "@/components/GrantFindingsCard";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";
import { cx } from "@/components/classes";

/** A list of finding cards: one per row, lines between, 16px 24px inside each. */
const FINDINGS_LIST =
  "m-0 list-none p-0 " +
  "[&>li]:flex [&>li]:flex-wrap [&>li]:items-start [&>li]:gap-4 [&>li]:border-t [&>li]:border-t-secondary [&>li]:px-6 [&>li]:py-4 [&>li:first-child]:border-t-0 " +
  "[&_.sev-dot]:mt-[7px]";

/** Findings on every grant this principal holds, directly or through a group: one card per grant. */
export default function PrincipalFindingsTab({
  findings,
  onChanged,
}: {
  findings: CurrentFinding[];
  onChanged: () => void;
}) {
  const { showToast } = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [showDecided, setShowDecided] = useState(false);
  const visible = showDecided ? findings : findings.filter((f) => f.status === "open");
  const decided = findings.length - findings.filter((f) => f.status === "open").length;
  const groups = groupByGrant(visible);

  async function run(key: string, action: () => Promise<unknown>, label: string) {
    setBusy(key);
    try {
      await action();
      showToast(label, "success");
      onChanged();
    } catch (e) {
      showToast(`Could not save: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <SectionCard
      icon="look"
      title="Needs a look"
      hint="One card per grant with all its reasons; deciding the card decides them all. Worked out again after each daily check."
    >
      <div className={FILTER_BAR}>
        <label className={pillClass(showDecided)}>
          <input type="checkbox" checked={showDecided} onChange={(e) => setShowDecided(e.target.checked)} />
          Include decided ({decided})
        </label>
        {groups.length > 0 && <span className={cx(CAPTION, "text-quaternary")}>{groupCountText(groups)}</span>}
        <span className={cx(CAPTION, "text-quaternary")}>
          Items on a group&apos;s grants are shared by every member: deciding one here decides it for all.
        </span>
      </div>
      {groups.length === 0 ? (
        <p className={cx("my-[1em]", EMPTY_STATE)}>
          {findings.length === 0 ? "Nothing to look at on this identity's grants." : "Nothing open: all decided."}
        </p>
      ) : (
        <ul className={FINDINGS_LIST}>
          {groups.map((g) => (
            <GrantFindingsCard
              key={g.key}
              group={g}
              busy={busy === g.key}
              showPrincipal
              onDecide={(fs, body: DecisionWrite, label) =>
                run(g.key, () => api.findings.decideMany(fs.map((f) => f.fingerprint), body), label)
              }
              onReopen={(fs) =>
                run(g.key, () => Promise.all(fs.map((f) => api.findings.reopen(f.fingerprint))), "Reopened")
              }
            />
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
