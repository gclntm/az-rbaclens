import type { Elbow, Guide, Guides } from "./treeGuides";

/** A vertical line down the middle of a guide; "last" stops it halfway. */
const LINE = "before:absolute before:box-content before:top-0 before:left-[7px] before:[border-left:1px_solid_var(--tree-line)] before:content-['']";
/** The short line across to the row. */
const ARM = "after:absolute after:box-content after:top-1/2 after:left-[7px] after:w-2 after:[border-top:1px_solid_var(--tree-line)] after:content-['']";
const TG = "relative w-4 flex-none";
const KIND: Record<Guide | Exclude<Elbow, null>, string> = {
  pipe: `${TG} ${LINE} before:bottom-0`,
  blank: TG,
  tee: `${TG} ${LINE} before:bottom-0 ${ARM}`,
  last: `${TG} ${LINE} before:bottom-1/2 ${ARM}`,
};

/** The connector lines in front of a tree row. */
export default function TreeLines({ guides, elbow }: Guides) {
  if (!elbow && guides.length === 0) return null;
  return (
    <span className="inline-flex flex-none self-stretch [--tree-line:var(--text-muted)]" aria-hidden="true">
      {guides.map((g, i) => (
        <span key={i} className={KIND[g]} />
      ))}
      {elbow && <span className={KIND[elbow]} />}
    </span>
  );
}
