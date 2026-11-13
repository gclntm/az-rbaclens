import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import Button from "@/components/Button";
import { api, type ScopeSnapshot } from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import { SectionCard } from "@/components/PageTemplate";
import ChangesFeed from "@/pages/changes/ChangesFeed";
import {
  BADGE,
  BADGE_LEVEL,
  BADGE_TONE,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  NUM,
  TABLE_COMPACT,
  TABLE_ROW_SELECTED,
  TABLE_SCROLL,
} from "@/components/classes";
import { cx } from "@/components/classes";

import { formatDateTime, LEVEL_SHORT, scopeHref, scopeLabel } from "./scopeUtils";

/** A place's Changes tab: the feed for it and everything inside, then the checks that covered it. */
export default function ScopeHistoryTab({ scopeId }: { scopeId: string }) {
  return (
    <>
      <SectionCard icon="changes" title="Changes" hint="Access given or removed here and inside, newest first.">
        <ChangesFeed scope={scopeId} summary emptyText="No change to access here in this period." />
      </SectionCard>
      <SectionCard
        icon="history"
        title="Daily checks of this place"
        hint="Each check is compared with the one before to find what changed. A check started from a place above also covers this one."
      >
        <ChecksTable scopeId={scopeId} />
      </SectionCard>
    </>
  );
}

/** Checks that covered this place: started from it or from a place above. */
function ChecksTable({ scopeId }: { scopeId: string }) {
  const [rows, setRows] = useState<ScopeSnapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    api.scopes
      .history(scopeId)
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, [scopeId]);

  if (error) return <p className="my-[1em] text-error-primary">Could not load the checks: {error}</p>;
  if (!rows) return <p className="my-[1em] text-quaternary">Loading checks…</p>;

  return (
    <div>
      {rows.length === 0 ? (
        <p className={cx("my-[1em]", EMPTY_STATE)}>No daily check has covered this place yet.</p>
      ) : (
        <div className={TABLE_SCROLL}>
          <table className={TABLE_COMPACT}>
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">Started from</th>
                <th scope="col">Result</th>
                <th scope="col">Finished</th>
                <th scope="col" className={NUM}>Grants</th>
                <th scope="col">
                  <span className="sr-only [clip:rect(0,0,0,0)]">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className={s.is_current ? TABLE_ROW_SELECTED : undefined}>
                  <td>
                    <div className={CELL_STACK}>
                      <span>#{s.id}</span>
                      {s.is_current && <span className={CELL_SUB}>the one shown now</span>}
                    </div>
                  </td>
                  <td>
                    <span className={cx(BADGE, BADGE_LEVEL[s.scope_level])}>{LEVEL_SHORT[s.scope_level]}</span>{" "}
                    {s.scope === scopeId ? (
                      <span>{scopeLabel(s.scope, s.scope_name)} (this place)</span>
                    ) : (
                      <Link to={scopeHref(s.scope)}>{scopeLabel(s.scope, s.scope_name)}</Link>
                    )}
                  </td>
                  <td>
                    <span className={cx(BADGE, BADGE_TONE[s.status as keyof typeof BADGE_TONE])} title={s.error ?? undefined}>
                      {s.status}
                    </span>
                  </td>
                  <td>{formatDateTime(s.finished_at ?? s.created_at)}</td>
                  <td className={NUM}>{s.role_assignment_count}</td>
                  <td>
                    {s.status === "done" && (
                      <div className="flex flex-[0_1_auto] flex-wrap gap-1">
                        {s.previous_done_id !== null && (
                          <Button size="sm" to={`/iam-access/diff/${s.previous_done_id}/${s.id}`}>
                            What changed since #{s.previous_done_id}
                          </Button>
                        )}
                        <DownloadLink href={api.iam.exportUrl(s.id, "csv")}>
                          CSV
                        </DownloadLink>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
