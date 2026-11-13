import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, type Freshness } from "@/api/client";
import { useActivePolling } from "@/hooks/useActivePolling";
import { isRestricted, useMe } from "@/hooks/useMe";
import { agoText } from "@/pages/grants/grantUtils";

const POLL_MS = 5 * 60_000;

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** "today 06:02", "yesterday 06:01", "3 Nov 06:02". */
export function whenText(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diff = Math.round((today - day) / 86_400_000);
  if (diff === 0) return `today ${clock(iso)}`;
  if (diff === 1) return `yesterday ${clock(iso)}`;
  return `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${clock(iso)}`;
}

/** Activity age in minutes or hours, then days: "40 min ago", "5 h ago", "3 days ago". */
export function activityAge(iso: string, now: Date = new Date()): string {
  const min = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (min < 60) return `${min} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return agoText(iso, now);
}

/** How fresh the picture is, in the side menu (GET /freshness). */
export default function FreshnessLine() {
  const [data, setData] = useState<Freshness | null>(null);
  const me = useMe();

  const load = useCallback(() => {
    api
      .freshness()
      .then(setData)
      .catch(() => setData(null));
  }, []);
  useEffect(load, [load]);
  useActivePolling(load, POLL_MS, true);

  if (!data) return null;
  const failed = data.last_check?.status === "failed";
  const problems = data.warnings?.length ?? 0;
  // Fresh = checked in the last 26 hours (a daily check plus slack).
  const stale = !data.access_as_of || Date.now() - new Date(data.access_as_of).getTime() > 26 * 3_600_000;
  const warn = failed || stale || problems > 0;
  // On narrow screens only the first line and what is wrong stay.
  const quiet = "pl-3.5 max-[860px]:hidden";
  const bad = "pl-3.5 text-warning-primary max-[860px]:pl-0";
  const lines = (
    <>
      <span className="flex items-center gap-1.5 font-medium text-primary">
        <span
          className={`size-2 flex-none rounded-full ${warn ? "bg-fg-warning-primary" : "bg-fg-success-primary"}`}
          aria-hidden="true"
        />
        Access as of {data.access_as_of ? whenText(data.access_as_of) : "— not checked yet"}
      </span>
      <span className={quiet}>
        Activity up to {data.activity_up_to ? activityAge(data.activity_up_to) : "— none collected"}
      </span>
      {failed && <span className={bad}>The last daily check failed</span>}
      {problems > 0 && (
        <span className={bad}>
          {problems === 1 ? "1 problem keeps" : `${problems} problems keep`} the data from updating
        </span>
      )}
      {!failed && data.next_check_at && <span className={quiet}>Next check {whenText(data.next_check_at)}</span>}
    </>
  );
  const box =
    "flex flex-col gap-0.5 rounded-md px-2 py-1 text-xs leading-snug text-tertiary " +
    "max-[860px]:flex-row max-[860px]:flex-wrap max-[860px]:gap-x-4 max-[860px]:gap-y-0 max-[860px]:p-0";
  if (isRestricted(me)) return <div className={box}>{lines}</div>;
  return (
    <Link
      to="/admin/data"
      className={`${box} outline-focus-ring hover:bg-primary_hover hover:text-secondary focus-visible:outline-2`}
      title="Data & schedules"
    >
      {lines}
    </Link>
  );
}
