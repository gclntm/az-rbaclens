// Types only: the library itself (about a third of the app's code) is loaded
// when sign-in is on, not for every visit (see init below).
import type { AccountInfo, PublicClientApplication } from "@azure/msal-browser";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import Button from "@/components/Button";
import { CAPTION, CARD, cx, SECTION_TITLE } from "@/components/classes";
import { api, type AuthConfigPublic, setApiTokenProvider } from "@/api/client";

type AuthState =
  | { status: "loading" }
  | { status: "disabled" }
  | { status: "signed-out"; signIn: () => void }
  | {
      status: "signed-in";
      account: AccountInfo;
      signOut: () => void;
    }
  | { status: "error"; message: string };

const AuthCtx = createContext<AuthState>({ status: "loading" });

export function useAuth(): AuthState {
  return useContext(AuthCtx);
}

/**
 * Bootstraps MSAL based on /api/auth/config.
 *
 * - When the backend reports ``enabled=false`` we set the API client's
 *   token provider to a no-op and the children render without any
 *   sign-in step (identical to the pre-auth build).
 * - When enabled we initialize an MSAL ``PublicClientApplication`` for
 *   the configured tenant + client_id, attempt a silent token
 *   acquisition, and (if needed) trigger a login redirect.
 *
 * The token provider acquires a fresh token on each API call via
 * ``acquireTokenSilent``. MSAL handles caching + refresh internally.
 * MSAL is imported only once sign-in is known to be on.
 */
export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const pcaRef = useRef<PublicClientApplication | null>(null);
  const msalRef = useRef<typeof import("@azure/msal-browser") | null>(null);
  const configRef = useRef<AuthConfigPublic | null>(null);

  const acquireToken = useCallback(
    async (opts?: { forceRefresh?: boolean }): Promise<string | null> => {
      const pca = pcaRef.current;
      const config = configRef.current;
      if (!pca || !config || !config.enabled || !config.client_id) return null;
      const accounts = pca.getAllAccounts();
      if (accounts.length === 0) return null;
      const scopes = [`${config.client_id}/.default`];
      try {
        const result = await pca.acquireTokenSilent({
          account: accounts[0],
          scopes,
          forceRefresh: opts?.forceRefresh ?? false,
        });
        return result.accessToken;
      } catch (err) {
        if (msalRef.current && err instanceof msalRef.current.InteractionRequiredAuthError) {
          await pca.acquireTokenRedirect({ scopes });
        }
        return null;
      }
    },
    [],
  );

  useEffect(() => {
    setApiTokenProvider(acquireToken);
  }, [acquireToken]);

  useEffect(() => {
    let cancelled = false;
    async function init() {
      let config: AuthConfigPublic;
      try {
        config = await api.auth.getConfig();
      } catch (err) {
        if (!cancelled) {
          setState({
            status: "error",
            message: `Could not load auth config: ${(err as Error).message}`,
          });
        }
        return;
      }
      configRef.current = config;
      if (!config.enabled || !config.tenant_id || !config.client_id) {
        if (!cancelled) setState({ status: "disabled" });
        return;
      }
      let msal: typeof import("@azure/msal-browser");
      try {
        msal = await import("@azure/msal-browser");
      } catch (err) {
        if (!cancelled) {
          setState({ status: "error", message: `Could not load sign-in: ${(err as Error).message}` });
        }
        return;
      }
      if (cancelled) return;
      msalRef.current = msal;
      const pca = new msal.PublicClientApplication({
        auth: {
          clientId: config.client_id,
          authority: config.authority ?? undefined,
          redirectUri: window.location.origin,
        },
        cache: { cacheLocation: "sessionStorage" },
      });
      pcaRef.current = pca;
      try {
        await pca.initialize();
        const redirectResult = await pca.handleRedirectPromise();
        const account =
          redirectResult?.account ?? pca.getAllAccounts()[0] ?? null;
        if (!account) {
          if (!cancelled) {
            setState({
              status: "signed-out",
              signIn: () =>
                pca.loginRedirect({
                  scopes: [`${config.client_id!}/.default`],
                }),
            });
          }
          return;
        }
        pca.setActiveAccount(account);
        if (!cancelled) {
          setState({
            status: "signed-in",
            account,
            signOut: () =>
              pca.logoutRedirect({
                postLogoutRedirectUri: window.location.origin,
              }),
          });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            status: "error",
            message: `MSAL init failed: ${(err as Error).message}`,
          });
        }
      }
    }
    init();
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo(() => state, [state]);
  return (
    <AuthCtx.Provider value={value}>
      <AuthGate state={state}>{children}</AuthGate>
    </AuthCtx.Provider>
  );
}

/**
 * Render children only when sign-in isn't required (auth disabled) or
 * the user is signed in. Otherwise show a full-page placeholder so
 * the rest of the app doesn't mount and start firing API calls that
 * will all 401.
 */
function AuthGate({
  state,
  children,
}: {
  state: AuthState;
  children: React.ReactNode;
}) {
  if (state.status === "disabled" || state.status === "signed-in") {
    return <>{children}</>;
  }
  return (
    <div className="flex min-h-screen items-center justify-center p-8 print:hidden!">
      <div className={cx(CARD, "flex w-full max-w-[420px] flex-col gap-4 text-center")}>
        <h2 className={SECTION_TITLE}>RBAC Lens</h2>
        {state.status === "loading" && (
          <p className="my-[1em] text-quaternary">Loading sign-in configuration…</p>
        )}
        {state.status === "error" && (
          <>
            <p className="my-[1em] text-error-primary">{state.message}</p>
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
              The auth config endpoint must be reachable for the app to
              boot. Check your reverse proxy / network and try again.
            </p>
          </>
        )}
        {state.status === "signed-out" && (
          <>
            <p className="my-[1em]">This deployment requires sign-in with Microsoft Entra ID.</p>
            <span className="self-center">
              <Button variant="primary" type="button" onClick={state.signIn}>
                Sign in with Microsoft
              </Button>
            </span>
          </>
        )}
      </div>
    </div>
  );
}
