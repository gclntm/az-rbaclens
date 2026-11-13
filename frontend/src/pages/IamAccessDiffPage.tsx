import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, type RoleAssignment, type ScanDiff } from "@/api/client";
import {
  BADGE,
  BADGE_LEVEL,
  CARD,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  MONO,
  TABLE_COMPACT,
  TABLE_SCROLL,
} from "@/components/classes";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { formatDateTime, LEVEL_SHORT, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

const HISTORY = (scope: string) => scopeHref(scope, "history");

/** One grant: what role, to whom, where. */
function GrantRows({ rows }: { rows: RoleAssignment[] }) {
  return (
    <div className={TABLE_SCROLL}>
      <table className={TABLE_COMPACT}>
        <thead>
          <tr>
            <th scope="col">Role</th>
            <th scope="col">Given to</th>
            <th scope="col">Where</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ra) => (
            <tr key={`${ra.scope}:${ra.role_definition_id}:${ra.principal_id}`}>
              <td>{ra.role_name ?? <span className={MONO}>{ra.role_definition_id.split("/").pop()}</span>}</td>
              <td>
                <div className={CELL_STACK}>
                  <Link to={principalHref(ra.principal_id)}>{ra.principal_name ?? ra.principal_id}</Link>
                  <span className={CELL_SUB}>{typeLabel(ra.principal_type)}</span>
                </div>
              </td>
              <td>
                <span className={cx(BADGE, BADGE_LEVEL[ra.scope_level])}>{LEVEL_SHORT[ra.scope_level]}</span>{" "}
                <Link to={scopeHref(ra.scope)} title={ra.scope}>
                  {scopeLabel(ra.scope, ra.scope_name)}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What changed in the grants of one place between two of its checks. */
export default function IamAccessDiffPage() {
  const { oldId, newId } = useParams();
  const [diff, setDiff] = useState<ScanDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const oldN = Number(oldId);
    const newN = Number(newId);
    if (!Number.isInteger(oldN) || !Number.isInteger(newN)) {
      setError("These are not check numbers.");
      return;
    }
    setDiff(null);
    setError(null);
    api.iam
      .diffScans(oldN, newN)
      .then(setDiff)
      .catch((e: Error) => setError(e.message));
  }, [oldId, newId]);

  if (error) {
    return (
      <section className={cx(CARD, "max-w-[640px]")} role="alert">
        <h1 className="mx-0 mt-0 mb-2 text-[1.375rem] font-bold">Could not compare these checks</h1>
        <p className="mx-0 mt-0 mb-2 text-error-primary">{error}</p>
      </section>
    );
  }
  if (!diff) {
    return (
      <div className={CARD}>
        <p className="my-[1em] text-quaternary">Loading…</p>
      </div>
    );
  }

  const { old_scan: before, new_scan: after } = diff;
  const place = scopeLabel(after.scope, after.scope_name);
  return (
    <>
      <PageHeader
        back={{ to: HISTORY(after.scope), label: `History of ${place}` }}
        title="What changed between two checks"
        sub={
          <>
            Grants on <Link to={scopeHref(after.scope)}>{place}</Link> and everything inside it: check #{before.id} (
            {formatDateTime(before.finished_at)}) against check #{after.id} ({formatDateTime(after.finished_at)}).
          </>
        }
      />

      <StatusTiles>
        <StatusTile
          tone={diff.added.length ? "warn" : "ok"}
          icon="changes"
          label="New grants"
          value={diff.added.length}
          note={diff.added.length ? "Given between the two checks" : "None given"}
        />
        <StatusTile
          tone="info"
          icon="changes"
          label="Removed grants"
          value={diff.removed.length}
          note={diff.removed.length ? "Gone by the later check" : "None removed"}
        />
        <StatusTile tone="info" icon="shield" label="Unchanged" value={diff.kept_count} note="In both checks" />
      </StatusTiles>

      <SectionCard
        icon="changes"
        tone={diff.added.length ? "warn" : "ok"}
        title={`New grants (${diff.added.length})`}
        hint="In the later check, not in the earlier one."
      >
        {diff.added.length ? <GrantRows rows={diff.added} /> : <p className={cx("my-[1em]", EMPTY_STATE)}>No grant was given.</p>}
      </SectionCard>
      <SectionCard
        icon="changes"
        title={`Removed grants (${diff.removed.length})`}
        hint="In the earlier check, gone from the later one."
      >
        {diff.removed.length ? <GrantRows rows={diff.removed} /> : <p className={cx("my-[1em]", EMPTY_STATE)}>No grant was removed.</p>}
      </SectionCard>
    </>
  );
}
