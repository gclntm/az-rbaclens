import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type AuditEvent } from "@/api/client";
import { BADGE, BADGE_TONE, CAPTION, FORM_ROW, MONO, cx } from "@/components/classes";
import { SectionCard } from "@/components/PageTemplate";

/**
 * A plain settings table (Audit log, Database): quiet header, 12px 16px cells at the
 * top of the row, the row lit on hover. Not TABLE_COMPACT (other padding and alignment).
 */
export const TABLE =
  "w-full border-collapse text-[0.875rem] " +
  "[&_thead_th]:border-b [&_thead_th]:border-b-secondary [&_thead_th]:bg-secondary [&_thead_th]:px-4 [&_thead_th]:py-2.5 [&_thead_th]:text-left [&_thead_th]:text-[0.75rem] [&_thead_th]:font-semibold [&_thead_th]:normal-case [&_thead_th]:text-quaternary " +
  "[&_tbody_td]:border-b [&_tbody_td]:border-b-secondary [&_tbody_td]:px-4 [&_tbody_td]:py-3 [&_tbody_td]:align-top [&_tbody_td]:text-primary " +
  "[&_tbody_tr:hover]:bg-primary_hover [&_tbody_tr:last-child_td]:[border-bottom:none]";

const PAGE_SIZE = 100;

/** "snapshot_keep_days: 0 → 365 · …" for a change that says what it changed. */
export function detailText(detail: AuditEvent["detail"]): string {
  const after = detail?.after ?? {};
  const before = detail?.before ?? {};
  return Object.keys(after)
    .map((k) => `${k}: ${String(before[k] ?? "—")} → ${String(after[k])}`)
    .join(" · ");
}

export default function AuditTab() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  async function refresh(nextOffset = 0) {
    setLoading(true);
    setError(null);
    try {
      const page = await api.audit.listEvents(PAGE_SIZE, nextOffset);
      setEvents(nextOffset === 0 ? page : [...events, ...page]);
      setOffset(nextOffset + page.length);
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SectionCard
      icon="history"
      title="Audit log"
      actions={
        <>
          <Button
            size="sm"
            type="button"
            onClick={() => refresh(0)}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </>
      }
    >
      <p className="my-[1em] text-quaternary">
        One row per mutating API call (POST / PUT / PATCH / DELETE under{" "}
        <code>/api/*</code>), with the address it came from. Read-only
        requests are not recorded.
        Body content is intentionally <strong>not</strong> stored — for a
        "did someone change the secret" check, look for a{" "}
        <code>PUT /api/settings/credentials</code> entry around the
        suspect time.
      </p>

      {error && <p className="my-[1em] text-error-primary">{error}</p>}

      {events.length === 0 ? (
        <p className="my-[1em] text-quaternary">No audit events recorded yet.</p>
      ) : (
        <table className={cx(TABLE, "mt-2")}>
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>From</th>
              <th>Method</th>
              <th>Path</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td className="whitespace-nowrap">
                  {new Date(e.occurred_at).toLocaleString()}
                </td>
                <td className="max-w-60 overflow-hidden text-ellipsis whitespace-nowrap">
                  {e.actor_name ?? e.actor ?? "—"}
                  {e.actor_name && e.actor && e.actor !== e.actor_name && (
                    <span className={cx(CAPTION, "text-quaternary", MONO)} title={e.actor}>
                      {" "}
                      {e.actor.slice(0, 8)}…
                    </span>
                  )}
                </td>
                <td className={MONO}>{e.client_ip ?? "—"}</td>
                <td>
                  <code>{e.method}</code>
                </td>
                <td className={cx(MONO, "max-w-[360px]")} title={e.path}>
                  <span className="block overflow-hidden text-ellipsis whitespace-nowrap">{e.path}</span>
                  {detailText(e.detail) && <span className="block whitespace-normal text-tertiary">{detailText(e.detail)}</span>}
                </td>
                <td>
                  <span
                    className={cx(BADGE, statusBadge(e.status_code))}
                    title={`HTTP ${e.status_code}`}
                  >
                    {e.status_code}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {hasMore && (
        <div className={FORM_ROW} style={{ marginTop: "var(--space-md)" }}>
          <Button
            size="sm"
            type="button"
            onClick={() => refresh(offset)}
            disabled={loading}
          >
            Load more
          </Button>
        </div>
      )}
    </SectionCard>
  );
}

function statusBadge(code: number): string {
  if (code >= 500) return BADGE_TONE.failed;
  if (code >= 400) return BADGE_TONE.running;
  return BADGE_TONE.done;
}
