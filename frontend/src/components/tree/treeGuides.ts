/**
 * Connector lines for a tree shown as a flat list of rows (depth-first).
 *
 * For each row: one guide per ancestor level ("pipe" when that ancestor
 * has a later sibling, so its line continues past this row, else "blank"),
 * then an elbow for the row itself ("tee" when it has a later sibling,
 * "last" when it is its parent's last child). Roots get no elbow.
 */
export type Guide = "pipe" | "blank";
export type Elbow = "tee" | "last" | null;
export type Guides = { guides: Guide[]; elbow: Elbow };

export function treeGuides(rows: ReadonlyArray<{ depth: number }>): Guides[] {
  const isLast = rows.map((row, i) => {
    for (let j = i + 1; j < rows.length; j++) {
      if (rows[j].depth < row.depth) return true;
      if (rows[j].depth === row.depth) return false;
    }
    return true;
  });
  const open: boolean[] = []; // open[d]: the ancestor at depth d has a later sibling
  return rows.map((row, i) => {
    open.length = row.depth;
    const guides: Guide[] = [];
    for (let d = 1; d < row.depth; d++) guides.push(open[d] ? "pipe" : "blank");
    const elbow: Elbow = row.depth === 0 ? null : isLast[i] ? "last" : "tee";
    open[row.depth] = !isLast[i];
    return { guides, elbow };
  });
}
