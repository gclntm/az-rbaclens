import { useEffect, useRef } from "react";

/**
 * Polling hook with two power-saving rules:
 *
 * 1. **Status-aware** — pass `enabled: false` once the upstream is in a
 *    terminal state and the loop unsubscribes. Re-enabling fires `tick`
 *    immediately rather than waiting a full interval.
 * 2. **Visibility-aware** — when the tab goes hidden the interval
 *    pauses; when it comes back, `tick` runs immediately and the
 *    interval resumes. This is what we want for a long-running
 *    operator dashboard: nobody benefits from polling a backgrounded
 *    tab every 2s, and nobody wants stale data when they come back.
 *
 * The `tick` ref is kept up-to-date without retriggering the effect, so
 * callers can pass an inline closure without worrying about dep churn.
 */
export function useActivePolling(
  tick: () => void,
  intervalMs: number,
  enabled: boolean,
): void {
  const tickRef = useRef(tick);
  tickRef.current = tick;

  useEffect(() => {
    if (!enabled) return;

    let handle: number | null = null;
    const start = () => {
      if (handle != null) return;
      handle = window.setInterval(() => tickRef.current(), intervalMs);
    };
    const stop = () => {
      if (handle != null) {
        clearInterval(handle);
        handle = null;
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        tickRef.current();
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, intervalMs]);
}
