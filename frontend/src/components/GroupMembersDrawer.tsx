import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Link } from "react-router-dom";

import { api, type GroupMembersResult } from "@/api/client";
import { CAPTION, cx, MONO } from "@/components/classes";
import Drawer, { DRAWER_META, DRAWER_SECTION, DRAWER_SECTION_TITLE } from "@/components/Drawer";
import { principalHref } from "@/pages/principals/principalUtils";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** A name that links to a person or app: plain text, blue with a dashed underline on hover. */
export const PRINCIPAL_LINK =
  "text-inherit no-underline [border-bottom:1px_dashed_transparent] [transition:color_80ms_ease,border-bottom-color_80ms_ease] " +
  "hover:text-(--accent-blue) hover:[border-bottom-color:var(--accent-blue)]";

/**
 * App-wide group-members drawer.
 *
 * Counterpart to {@link RoleDetailDrawer}: mount once at the App
 * root, descendants call ``useGroupMembers().open(scanId, groupId,
 * groupName?)`` to show the transitive membership of a group
 * captured during a scan. Used from the Coverage tab's
 * "via N members" caption so reviewers can drill into *who* is
 * actually exercising a group's role.
 *
 * In-session cache keyed on ``(scanId, groupId)`` — re-opening the
 * same group doesn't re-hit the API.
 */

type OpenArgs = {
  scanId: number;
  groupId: string;
  groupName?: string | null;
};
type Ctx = { open: (args: OpenArgs) => void };
const GroupMembersContext = createContext<Ctx | null>(null);

export function useGroupMembers(): Ctx {
  const ctx = useContext(GroupMembersContext);
  if (!ctx) {
    throw new Error(
      "useGroupMembers must be used within a GroupMembersProvider",
    );
  }
  return ctx;
}

function cacheKey(scanId: number, groupId: string): string {
  return `${scanId}:${groupId.toLowerCase()}`;
}

export function GroupMembersProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [args, setArgs] = useState<OpenArgs | null>(null);
  const [data, setData] = useState<GroupMembersResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cache] = useState(() => new Map<string, GroupMembersResult>());

  const open = useCallback((next: OpenArgs) => {
    setArgs(next);
  }, []);
  const close = useCallback(() => {
    setArgs(null);
    setData(null);
    setError(null);
  }, []);

  useEffect(() => {
    if (!args) return;
    const key = cacheKey(args.scanId, args.groupId);
    const hit = cache.get(key);
    if (hit) {
      setData(hit);
      setError(null);
      return;
    }
    setLoading(true);
    setData(null);
    setError(null);
    api.iam
      .getGroupMembers(args.scanId, args.groupId)
      .then((d) => {
        cache.set(key, d);
        setData(d);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [args, cache]);

  const ctx = useMemo(() => ({ open }), [open]);

  return (
    <GroupMembersContext.Provider value={ctx}>
      {children}
      {args && (
        <Drawer title={args.groupName ?? "Group members"} onClose={close}>
          <dl className={DRAWER_META}>
            <div>
              <dt>Group ID</dt>
              <dd className={cx(MONO, "text-quaternary")}>{args.groupId}</dd>
            </div>
            <div>
              <dt>From check</dt>
              <dd>#{args.scanId}</dd>
            </div>
          </dl>

          <Body loading={loading} error={error} data={data} />
        </Drawer>
      )}
    </GroupMembersContext.Provider>
  );
}

function Body({
  loading,
  error,
  data,
}: {
  loading: boolean;
  error: string | null;
  data: GroupMembersResult | null;
}) {
  const [filter, setFilter] = useState("");

  if (loading) return <p className="my-[1em] text-quaternary">Loading…</p>;
  if (error) {
    return (
      <div>
        <p className="my-[1em] text-error-primary">Could not load membership.</p>
        <p className={cx("my-[1em]", CAPTION_MUTED)}>{error}</p>
      </div>
    );
  }
  if (!data) return null;

  const needle = filter.trim().toLowerCase();
  const filtered = needle
    ? data.members.filter(
        (m) =>
          (m.member_name ?? "").toLowerCase().includes(needle)
          || m.member_id.toLowerCase().includes(needle)
          || (m.member_type ?? "").toLowerCase().includes(needle),
      )
    : data.members;

  return (
    <section className={DRAWER_SECTION}>
      <h4 className={DRAWER_SECTION_TITLE}>
        Members{" "}
        <span className={CAPTION_MUTED}>
          ({filtered.length}
          {filter ? ` of ${data.members.length}` : ""})
        </span>
      </h4>
      {data.members.length === 0 ? (
        <p className="my-[1em] text-quaternary">
          Graph confirmed this group has zero transitive members at
          scan time.
        </p>
      ) : (
        <>
          <input
            type="search"
            className="font-normal tracking-normal mb-2 w-full"
            placeholder="Filter by name, ID, or type…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {filtered.map((m) => (
              <li key={m.member_id} className="grid grid-cols-[max-content_1fr_auto] items-baseline gap-x-2 border-b border-b-secondary py-1 text-[0.8125rem] last:[border-bottom:none]">
                <span className={cx(CAPTION_MUTED, "min-w-[8ch]")}>
                  {m.member_type ?? "—"}
                </span>
                <Link
                  to={principalHref(m.member_id)}
                  className={cx(PRINCIPAL_LINK, "overflow-hidden text-ellipsis whitespace-nowrap")}
                  title={m.member_id}
                >
                  {m.member_name ?? m.member_id}
                </Link>
                {m.member_name && (
                  <span
                    className={cx(MONO, "text-quaternary", "max-w-[32ch] overflow-hidden text-[0.6875rem] text-ellipsis whitespace-nowrap")}
                    title={m.member_id}
                  >
                    {m.member_id}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
