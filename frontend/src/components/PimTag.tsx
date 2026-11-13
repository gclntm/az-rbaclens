import { cx, TAG, TAG_TONE } from "@/components/classes";
import { formatDay } from "@/pages/scopes/whoUtils";

/** Marks a grant switched on through PIM and/or one that ends on a date. */
export default function PimTag({ activated, until }: { activated?: boolean; until?: string | null }) {
  if (!activated && !until) return null;
  const label = [activated ? "PIM" : null, until ? `until ${formatDay(until)}` : null].filter(Boolean).join(" · ");
  return (
    <span
      className={cx(TAG, TAG_TONE.pim)}
      title={activated ? "Eligible role activated through PIM" : "A grant that ends on a date"}
    >
      {label}
    </span>
  );
}
