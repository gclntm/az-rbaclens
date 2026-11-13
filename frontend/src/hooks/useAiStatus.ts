import { useEffect, useState } from "react";

import { api } from "@/api/client";

let cached: Promise<boolean> | null = null;

/** Whether optional AI wording is on and has a key (GET /ai/status), once per page load. */
export function useAiAvailable(): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    let alive = true;
    cached ??= api.ai
      .status()
      .then((s) => s.available)
      .catch(() => {
        cached = null;
        return false;
      });
    cached.then((v) => alive && setOk(v));
    return () => {
      alive = false;
    };
  }, []);
  return ok;
}

/** After the settings change, the next page asks again. */
export function forgetAiStatus(): void {
  cached = null;
}
