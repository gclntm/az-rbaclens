import type { ConditionKind } from "@/api/client";
import { cx, TAG, TAG_TONE } from "@/components/classes";

/**
 * Marks a role assignment narrowed by an ABAC condition. The plain-language
 * summary is the tooltip; unrecognised conditions get the warning tone.
 */
export default function ConditionTag({
  kind,
  text,
}: {
  kind: ConditionKind | null | undefined;
  text: string | null | undefined;
}) {
  if (!kind) return null;
  return (
    <span className={cx(TAG, kind === "other" ? TAG_TONE.conditionOther : TAG_TONE.condition)} title={text ?? undefined}>
      {kind === "other" ? "condition?" : "limited"}
    </span>
  );
}
