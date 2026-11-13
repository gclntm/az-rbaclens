import { Badge } from "@/ui/untitled/components/base/badges/badges";
import { Button } from "@/ui/untitled/components/base/buttons/button";

import { useAuth } from "./AuthProvider";

const ROW = "flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-tertiary";

/**
 * Auth-status widget for the sidebar footer. Always renders
 * *something* so the operator can see at a glance which mode the
 * app is running in — even ``disabled`` is meaningful info
 * (no sign-in required on this stage). The earlier "render nothing
 * when disabled" behaviour hid a real state from the reviewer.
 */
export default function AuthStatus() {
  const auth = useAuth();

  if (auth.status === "loading") {
    return <span className={ROW}>Auth: loading…</span>;
  }

  if (auth.status === "disabled") {
    return (
      <span
        className={`${ROW} whitespace-nowrap`}
        title="Sign-in is off (Settings › Authentication). Anyone who reaches the app can use it, as an admin."
      >
        <span className="size-2 flex-none rounded-full bg-fg-warning-primary" aria-hidden="true" />
        Sign-in off
      </span>
    );
  }

  if (auth.status === "error") {
    return (
      <span className={`${ROW} text-error-primary`} title={auth.message}>
        Auth: error
      </span>
    );
  }

  const entra = (
    <span title={auth.status === "signed-out" ? "Entra ID sign-in is required." : "Entra ID sign-in is active."}>
      <Badge type="pill-color" size="sm" color="brand">
        Entra ID
      </Badge>
    </span>
  );

  if (auth.status === "signed-out") {
    return (
      <span className={ROW}>
        {entra}
        <Button size="xs" color="secondary" onPress={auth.signIn}>
          Sign in
        </Button>
      </span>
    );
  }

  const label = auth.account.username || auth.account.name || "signed in";
  return (
    <span className={ROW}>
      {entra}
      <span className="max-w-full truncate" title={auth.account.username ?? ""}>
        {label}
      </span>
      <Button size="xs" color="secondary" onPress={auth.signOut}>
        Sign out
      </Button>
    </span>
  );
}
