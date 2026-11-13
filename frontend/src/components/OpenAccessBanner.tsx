import { useState } from "react";

import Button from "@/components/Button";
import { useAuth } from "@/auth/AuthProvider";
import { FORM_ROW } from "@/components/classes";

const HIDDEN_KEY = "rbaclens.open-access-banner.hidden";

/** Whether the page was opened by a name that only this machine can use (localhost, 127.x, ::1). */
export function isThisMachine(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return h === "localhost" || h.endsWith(".localhost") || h === "::1" || /^127(\.\d{1,3}){3}$/.test(h);
}

function readHidden(): boolean {
  try {
    return window.sessionStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false; // private window, blocked storage: just show it
  }
}

/**
 * Shown on every page while sign-in is off and the page was opened by a network address:
 * whoever can open that address can see and change everything. Nothing on this machine's
 * own address (localhost). "Hide for now" lasts until the browser tab is closed.
 */
export default function OpenAccessBanner({ hostname = window.location.hostname }: { hostname?: string }) {
  const auth = useAuth();
  const [hidden, setHidden] = useState(readHidden);
  if (auth.status !== "disabled" || isThisMachine(hostname) || hidden) return null;

  function hide() {
    setHidden(true);
    try {
      window.sessionStorage.setItem(HIDDEN_KEY, "1");
    } catch {
      // not stored: it comes back on the next load, which is fine
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-[8px] border border-(--accent-red) bg-(--accent-red-subtle) px-6 py-4 text-[0.875rem] print:hidden!" role="alert">
      <span>
        <strong>Sign-in is off, and this address can be opened from the network.</strong> Anyone who opens it can
        see and change everything here.
      </span>
      <span className={FORM_ROW}>
        <Button size="sm" to="/settings/authentication">
          Turn sign-in on
        </Button>
        <Button size="sm" type="button" onClick={hide}>
          Hide for now
        </Button>
      </span>
    </div>
  );
}
