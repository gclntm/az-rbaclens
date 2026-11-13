/**
 * Pages load on demand (React.lazy). After a redeploy, a tab that was already
 * open asks for page files the new build no longer has. Vite reports that as
 * a "vite:preloadError" event; reload once so the tab picks up the new build.
 * A timestamp in sessionStorage stops a reload loop if the files are really
 * missing.
 */
const KEY = "rbaclens-stale-reload-at";
const MIN_GAP_MS = 10_000;

/** True when a reload is allowed now (none in the last 10 s); records it. */
export function shouldReload(now: number, storage: Pick<Storage, "getItem" | "setItem">): boolean {
  const last = Number(storage.getItem(KEY) ?? 0);
  if (now - last < MIN_GAP_MS) return false;
  storage.setItem(KEY, String(now));
  return true;
}

export function reloadOnStaleChunks(): void {
  window.addEventListener("vite:preloadError", (event) => {
    let allowed = true;
    try {
      allowed = shouldReload(Date.now(), sessionStorage);
    } catch {
      /* storage blocked: reload anyway, the browser's own loop protection applies */
    }
    if (!allowed) return; // let the error reach the error boundary
    event.preventDefault();
    window.location.reload();
  });
}
