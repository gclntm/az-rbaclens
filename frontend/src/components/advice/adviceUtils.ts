import type { AdviceAction, AdviceWeight, SuggestedDecision } from "@/api/client";

export const WEIGHT_LABEL: Record<AdviceWeight, string> = {
  must: "Must do",
  should: "Should do",
  could: "Could do",
  info: "Nothing to do",
};

export const ACTION_LABEL: Record<AdviceAction, string> = {
  remove: "Remove",
  pim: "Make PIM-eligible",
  replace: "Replace the role",
  narrow: "Give it lower down",
  ask: "Ask",
  fix_members: "Find out who is in the group",
  fix_owners: "Check who owns it",
  fix_credentials: "Replace or delete the secret",
  set_responsible: "Set someone responsible",
  keep: "Keep",
};

export const DECISION_LABEL: Record<SuggestedDecision, string> = {
  keep: "Keep",
  remove: "Remove",
  change: "Change",
  ask: "Ask",
};

/** ISO/IEC 27001:2022 Annex A controls the playbook names (core/advice_texts.py). */
export const CONTROL_NAME: Record<string, string> = {
  "5.15": "Access control",
  "5.16": "Identity management",
  "5.17": "Authentication information",
  "5.18": "Access rights",
  "8.2": "Privileged access rights",
  "8.15": "Logging",
};

export function controlsText(controls: string[]): string {
  return controls.map((c) => `A.${c} ${CONTROL_NAME[c] ?? ""}`.trim()).join(" · ");
}

/** "R02 standing-admin" → "R02". */
export function ruleId(rule: string): string {
  return rule.split(" ", 1)[0];
}

/** True when the advice asks for something to be done. */
export function actionable(weight: AdviceWeight): boolean {
  return weight !== "info";
}
