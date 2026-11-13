import type { Advice } from "@/api/client";
import { CAPTION, cx } from "@/components/classes";
import { SectionCard, type Tone } from "@/components/PageTemplate";

import AiExplain from "./AiExplain";
import { ACTION_LABEL, WEIGHT_LABEL, actionable, controlsText, ruleId } from "./adviceUtils";
import Commands from "./Commands";

/** How much a piece of advice weighs (must, should, could, info), as a small pill. */
export const ADVICE_WEIGHT =
  "flex-none rounded-full px-2 py-px text-[0.6875rem] font-semibold tracking-[0.03em] whitespace-nowrap uppercase print:[print-color-adjust:exact]";
export const ADVICE_WEIGHT_TONE: Record<string, string> = {
  must: "bg-(--accent-red-subtle) text-error-primary",
  should: "bg-(--accent-orange-subtle) text-warning-primary",
  could: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  info: "bg-(--bg-input) text-tertiary",
};
/** The same weight as a dot. */
export const ADVICE_DOT = "inline-block size-2 rounded-full bg-(--text-muted) align-middle print:[print-color-adjust:exact]";
export const ADVICE_DOT_TONE: Record<string, string> = {
  must: "bg-(--accent-red)",
  should: "bg-(--accent-orange)",
  could: "bg-(--accent-blue)",
};
/** "How": a label, then numbered steps. */
export const ADVICE_HOW = "[&_li]:my-0.5 [&_li]:[overflow-wrap:anywhere] [&_ol]:mx-0 [&_ol]:mt-1 [&_ol]:mb-2 [&_ol]:list-decimal [&_ol]:pl-5";
/** A closing line (also, ISO controls). */
export const ADVICE_NOTE = "mx-0 mt-2 mb-0";
export const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

const WEIGHT_TONE: Record<Advice["weight"], Tone> = { must: "danger", should: "warn", could: "info", info: "ok" };

/** The playbook's advice for one grant: the action, why, how, and the other rules that fired. */
export default function AdviceBox({ advice: a, grantId }: { advice: Advice; grantId: string }) {
  const todo = actionable(a.weight);
  return (
    <SectionCard
      icon="todo"
      tone={WEIGHT_TONE[a.weight]}
      title={todo ? a.title : "Nothing to do"}
      hint={a.why}
      actions={
        <>
          <span className={cx(ADVICE_WEIGHT, ADVICE_WEIGHT_TONE[a.weight])}>{WEIGHT_LABEL[a.weight]}</span>
          <span className={CAPTION_MUTED} title={a.rule}>
            {ruleId(a.rule)}
          </span>
        </>
      }
    >
      {todo && a.how.length > 0 && (
        <div className={ADVICE_HOW}>
          <span className={CAPTION_MUTED}>{ACTION_LABEL[a.action]}: how</span>
          <ol>
            {a.how.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
      <Commands commands={a.commands} />
      {a.also.length > 0 && (
        <p className={cx(CAPTION, ADVICE_NOTE)}>
          Also:{" "}
          {a.also.map((n, i) => (
            <span key={n.rule}>
              {i > 0 && " · "}
              <span className={cx(ADVICE_DOT, ADVICE_DOT_TONE[n.weight])} aria-hidden="true" /> {n.title}
            </span>
          ))}
        </p>
      )}
      {a.controls.length > 0 && <p className={cx(CAPTION_MUTED, ADVICE_NOTE)}>ISO 27001: {controlsText(a.controls)}</p>}
      <AiExplain target={{ grant_id: grantId }} />
    </SectionCard>
  );
}
