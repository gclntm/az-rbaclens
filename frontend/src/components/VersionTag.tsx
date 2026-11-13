import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { version as pageVersion } from "../../package.json";
import { api } from "@/api/client";
import { useActivePolling } from "@/hooks/useActivePolling";
import { isRestricted, useMe } from "@/hooks/useMe";

const POLL_MS = 5 * 60_000;
const TAG = "ml-auto text-xs tabular-nums max-[860px]:ml-0";

/** "same" when this page is the running version, "newer" when a deploy happened since it loaded. */
export function versionState(page: string, running: string | null): "same" | "newer" | "unknown" {
  if (!running) return "unknown";
  return running === page ? "same" : "newer";
}

/** The running version (GET /health) beside the name in the side menu; offers a reload after a deploy. */
export default function VersionTag() {
  const [running, setRunning] = useState<string | null>(null);
  const me = useMe();
  const load = useCallback(() => {
    api
      .health()
      .then((h) => setRunning(h.version ?? null))
      .catch(() => setRunning(null));
  }, []);
  useEffect(load, [load]);
  useActivePolling(load, POLL_MS, true);

  const state = versionState(pageVersion, running);
  if (state === "unknown") return null;
  if (state === "newer") {
    return (
      <button
        type="button"
        className={`${TAG} cursor-pointer font-semibold text-warning-primary outline-focus-ring hover:underline focus-visible:outline-2`}
        title={`Version ${running} is running; this page is ${pageVersion}. Reload to update.`}
        onClick={() => window.location.reload()}
      >
        v{running} · reload
      </button>
    );
  }
  const label = `v${running}`;
  if (isRestricted(me)) {
    return (
      <span className={`${TAG} text-quaternary`} title={`Running version ${running}`}>
        {label}
      </span>
    );
  }
  return (
    <Link
      to="/settings/changelog"
      className={`${TAG} text-quaternary outline-focus-ring hover:text-tertiary hover:underline focus-visible:outline-2`}
      title={`Running version ${running}: what's new`}
    >
      {label}
    </Link>
  );
}
