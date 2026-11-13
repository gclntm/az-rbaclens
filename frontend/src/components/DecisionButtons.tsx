import { useEffect, useId, useRef, useState } from "react";

import Button from "@/components/Button";
import { CAPTION } from "@/components/classes";
import type { DecisionWrite } from "@/api/client";
import { snoozeUntil } from "@/pages/scopes/scopeUtils";

/** The row of decision buttons. */
export const FEED_ACTIONS = "flex flex-[0_1_auto] flex-wrap gap-1";

export const SNOOZE_DAYS = 30;

/**
 * The decisions on what needs a look, the same wherever they appear: Mark
 * reviewed, Accept risk (with its reason, asked here, kept with the decision)
 * and Snooze; Reopen once decided. ``what`` names what the buttons decide,
 * e.g. "3 reasons", for the labels a screen reader hears and the toast.
 */
export default function DecisionButtons({
  open,
  busy,
  disabled = false,
  what,
  onDecide,
  onReopen,
}: {
  /** Something is still open: offer the decisions. Otherwise offer Reopen. */
  open: boolean;
  busy: boolean;
  disabled?: boolean;
  what?: string;
  onDecide: (body: DecisionWrite, label: string) => void;
  onReopen?: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const reasonId = useId();
  const off = busy || disabled;
  const label = (done: string) => (what ? `${done}: ${what}` : done);

  useEffect(() => {
    if (asking) input.current?.focus();
  }, [asking]);
  useEffect(() => {
    if (off) setAsking(false);
  }, [off]);

  if (!open) {
    return onReopen ? (
      <div className={FEED_ACTIONS}>
        <Button size="sm" type="button" disabled={off} onClick={onReopen}>
          Reopen
        </Button>
      </div>
    ) : null;
  }

  if (asking) {
    const text = reason.trim();
    return (
      <form
        className="flex max-w-[420px] min-w-0 flex-[1_1_260px] flex-col gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          if (!text) return;
          onDecide({ decision: "accepted_risk", note: text }, label("Risk accepted"));
          setAsking(false);
          setReason("");
        }}
      >
        <label htmlFor={reasonId} className={CAPTION}>
          Why is this risk acceptable{what ? ` (${what})` : ""}? Kept with the decision.
        </label>
        <input
          id={reasonId}
          ref={input}
          type="text"
          className="font-normal tracking-normal w-full"
          maxLength={1000}
          value={reason}
          placeholder="For example: break-glass account, owned by the security team"
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setAsking(false);
            }
          }}
        />
        <div className={FEED_ACTIONS}>
          <Button variant="primary" size="sm" type="submit" disabled={!text}>
            Accept risk
          </Button>
          <Button size="sm" type="button" onClick={() => setAsking(false)}>
            Cancel
          </Button>
        </div>
      </form>
    );
  }

  return (
    <div className={FEED_ACTIONS}>
      <Button
        size="sm"
        type="button"
        disabled={off}
        onClick={() => onDecide({ decision: "reviewed" }, label("Marked reviewed"))}
      >
        Mark reviewed
      </Button>
      <Button size="sm" type="button" disabled={off} onClick={() => setAsking(true)}>
        Accept risk…
      </Button>
      <Button
        size="sm"
        type="button"
        disabled={off}
        onClick={() =>
          onDecide({ decision: "snoozed", snoozed_until: snoozeUntil(SNOOZE_DAYS) }, label(`Snoozed for ${SNOOZE_DAYS} days`))
        }
      >
        Snooze {SNOOZE_DAYS} d
      </Button>
    </div>
  );
}
