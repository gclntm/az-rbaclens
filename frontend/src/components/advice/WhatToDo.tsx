import { useState } from "react";
import { Link } from "react-router-dom";

import type { IdentityAdvice } from "@/api/client";
import { cx, LEVEL_TAG } from "@/components/classes";
import { SectionCard } from "@/components/PageTemplate";
import { grantHref } from "@/pages/grants/grantUtils";
import { LEVEL_SHORT, scopeLabel } from "@/pages/scopes/scopeUtils";

import AiExplain from "./AiExplain";
import { ACTION_LABEL, WEIGHT_LABEL, controlsText, ruleId } from "./adviceUtils";
import { ADVICE_HOW, ADVICE_NOTE, ADVICE_WEIGHT, ADVICE_WEIGHT_TONE, CAPTION_MUTED } from "./AdviceBox";
import Commands from "./Commands";

/** The weight's colour, for the line down the side of an opened item. */
const ACCENT: Record<string, string> = {
  must: "[--advice-accent:var(--accent-red)]",
  should: "[--advice-accent:var(--accent-orange)]",
  could: "[--advice-accent:var(--accent-blue)]",
  info: "[--advice-accent:var(--accent-green,var(--border))]",
};
const TODO_LINE =
  "flex min-h-11 w-full cursor-pointer items-center gap-2 [border:none] bg-transparent px-0 py-1.5 text-left text-inherit [font:inherit] " +
  "focus-visible:rounded-[4px] focus-visible:[box-shadow:var(--focus-ring)] focus-visible:outline-none max-[640px]:flex-wrap";

/** "What to do" for a person, group or app: their own grants' advice, most important first. */
export default function WhatToDo({
  advice,
  principalId,
  microsoft = false,
}: {
  advice: IdentityAdvice;
  principalId: string;
  microsoft?: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const total = advice.actions.reduce((n, a) => n + a.grants.length, 0);

  if (advice.actions.length === 0) {
    return (
      <SectionCard icon="todo" tone="ok" title="What to do">
        <p className={CAPTION_MUTED} style={{ margin: 0 }}>
          {microsoft
            ? "Nothing: Microsoft runs this app for an Azure service (Defender for Cloud, AKS, backup…), which gave it these roles. Keep them while the service is in use; turning the service off removes them."
            : `Nothing: no rule of the playbook applies to ${advice.fine === 1 ? "their grant" : `their ${advice.fine} grants`} in their own name. Access through groups is advised on each group's page.`}
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      icon="todo"
      tone={
        advice.actions.some((a) => a.weight === "must")
          ? "danger"
          : advice.actions.some((a) => a.weight === "should")
            ? "warn"
            : "info"
      }
      title="What to do"
      hint={`The playbook's advice on ${total} of their grants in their own name; ${advice.fine} with nothing to do.`}
    >
      <ol className="m-0 list-none p-0">
        {advice.actions.map((a) => {
          const isOpen = open === a.rule;
          const commands = a.grants.flatMap((g) => g.commands);
          return (
            <li key={a.rule} className={cx("border-t border-t-secondary first:[border-top:none]", ACCENT[a.weight])}>
              <button
                type="button"
                className={cx("tracking-normal", TODO_LINE)}
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : a.rule)}
              >
                <span className={cx(ADVICE_WEIGHT, ADVICE_WEIGHT_TONE[a.weight])}>{WEIGHT_LABEL[a.weight]}</span>
                <span className="min-w-0 flex-[1_1_auto] font-medium">{a.title}</span>
                <span className={CAPTION_MUTED}>
                  {a.grants.length} grant{a.grants.length === 1 ? "" : "s"}
                </span>
                <span className="w-[1em] text-quaternary" aria-hidden="true">
                  {isOpen ? "▾" : "▸"}
                </span>
              </button>
              {isOpen && (
                <div className="ml-0.5 [border-left:3px_solid_var(--advice-accent)] pt-0 pr-0 pb-4 pl-4 max-[640px]:pl-2">
                  {a.why && <p className="mx-0 my-2">{a.why}</p>}
                  <ul className="mx-0 mt-0 mb-2 grid list-none gap-1 p-0">
                    {a.grants.map((g) => (
                      <li key={g.grant_id}>
                        <span className={LEVEL_TAG}>{LEVEL_SHORT[g.scope_level]}</span>{" "}
                        <Link to={grantHref(g.grant_id)}>
                          {g.role_name ?? "Unnamed role"} on {scopeLabel(g.scope, g.scope_name)}
                        </Link>
                      </li>
                    ))}
                  </ul>
                  {a.how.length > 0 && (
                    <div className={ADVICE_HOW}>
                      <span className={CAPTION_MUTED}>{ACTION_LABEL[a.action]}: how</span>
                      <ol>
                        {a.how.map((step) => (
                          <li key={step}>{step}</li>
                        ))}
                      </ol>
                    </div>
                  )}
                  <Commands commands={commands} />
                  <p className={cx(CAPTION_MUTED, ADVICE_NOTE)}>
                    {ruleId(a.rule)} · ISO 27001: {controlsText(a.controls)}
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      <AiExplain target={{ principal_id: principalId }} />
      {advice.more > 0 && (
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          And {advice.more} more kind{advice.more === 1 ? "" : "s"} of action, each shown on its grant's page.
        </p>
      )}
    </SectionCard>
  );
}
