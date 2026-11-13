import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type CredentialSource, type CredentialsRead } from "@/api/client";
import { CAPTION, CARD, FORM_ROW, KV_LIST, cx } from "@/components/classes";
import { useConfirm } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";

/** A settings form (Credentials, Database): one field under the other. */
export const SETTINGS_FORM = "flex max-w-160 flex-col gap-4";
/** A field (a label or a fieldset) with its small label above. */
export const SETTINGS_FIELD = "m-0 flex flex-col gap-1.5 p-0 [border:0]";
export const SETTINGS_LABEL = "p-0 text-[0.75rem] font-medium text-tertiary";

const SOURCE_LABEL: Record<CredentialSource, string> = {
  env: "Environment variables",
  db: "Database (entered via this page)",
  default: "Default Azure credential",
  none: "Not configured",
};

const SOURCE_DESC: Record<CredentialSource, string> = {
  env: "ARM_CLIENT_ID / ARM_CLIENT_SECRET / ARM_TENANT_ID are set on the backend. They take precedence over anything saved here.",
  db: "Credentials saved through this page. The secret is encrypted at rest with the server's ENCRYPTION_KEY.",
  default:
    "No env vars or saved credentials; falls through to DefaultAzureCredential (developer az login, pod identity, etc.).",
  none: "No env vars or saved credentials.",
};

export default function CredentialsTab() {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [state, setState] = useState<CredentialsRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form state — pre-filled from current values for tenant_id / client_id
  // (those are non-secret and we always have them when the source is env/db);
  // the secret field is always blank to avoid re-displaying it.
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [secretMode, setSecretMode] = useState<"keep" | "set" | "clear">("keep");

  async function refresh() {
    try {
      const next = await api.settings.getCredentials();
      setState(next);
      setTenantId(next.tenant_id ?? "");
      setClientId(next.client_id ?? "");
      setSecretMode("keep");
      setClientSecret("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        tenant_id: tenantId.trim(),
        client_id: clientId.trim(),
        client_secret:
          secretMode === "keep"
            ? null
            : secretMode === "clear"
              ? ""
              : clientSecret,
      };
      const next = await api.settings.putCredentials(payload);
      setState(next);
      setSecretMode("keep");
      setClientSecret("");
      showToast("Credentials saved.", "success");
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      showToast(`Save failed: ${msg}`, "error");
    } finally {
      setSubmitting(false);
    }
  }

  async function onClear() {
    const ok = await confirm({
      title: "Delete the saved Azure credentials?",
      body: "They are removed from the database. RBAC Lens then uses the environment or DefaultAzureCredential.",
      action: "Delete",
      danger: true,
    });
    if (!ok) return;
    setSubmitting(true);
    setError(null);
    try {
      const next = await api.settings.deleteCredentials();
      setState(next);
      setTenantId(next.tenant_id ?? "");
      setClientId(next.client_id ?? "");
      setSecretMode("keep");
      setClientSecret("");
      showToast("Saved credentials deleted.", "success");
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      showToast(`Delete failed: ${msg}`, "error");
    } finally {
      setSubmitting(false);
    }
  }

  if (!state) {
    return (
      <div className={CARD}>
        {error ? <p className="my-[1em] text-error-primary">Error: {error}</p> : <p className="my-[1em]">Loading…</p>}
      </div>
    );
  }

  const envActive = state.source === "env";
  const encryptionMissing = !state.encryption_available;

  return (
    <>
      <SectionCard
        icon="shield"
        title="Azure credentials"
      >
        <p className="my-[1em] text-quaternary">
          The backend resolves credentials in this order:{" "}
          <strong>env vars → saved settings → DefaultAzureCredential</strong>.
        </p>

        <ul className={KV_LIST}>
          <li>
            <span className={CAPTION}>Active source</span>
            <strong>{SOURCE_LABEL[state.source]}</strong>
          </li>
          <li>
            <span className={CAPTION}>Encryption</span>
            <strong>
              {state.encryption_available
                ? "available (ENCRYPTION_KEY configured)"
                : "unavailable — set ENCRYPTION_KEY to save secrets"}
            </strong>
          </li>
          {state.updated_at && (
            <li>
              <span className={CAPTION}>Saved row updated</span>
              <strong>{new Date(state.updated_at).toLocaleString()}</strong>
            </li>
          )}
        </ul>
        <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>{SOURCE_DESC[state.source]}</p>
      </SectionCard>

      <SectionCard
        icon="shield"
        title="Save credentials"
      >
        {envActive && (
          <p className="my-[1em] text-quaternary">
            Note: env vars are currently active and override anything saved here.
            Saving still works, but the saved values will only kick in once the
            env vars are unset.
          </p>
        )}
        {encryptionMissing && (
          <p className="my-[1em] text-error-primary">
            ENCRYPTION_KEY is not set on the backend. Saving the client secret
            will return a 503. Generate one with{" "}
            <code>
              python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
            </code>{" "}
            and set <code>ENCRYPTION_KEY</code> in <code>.env</code>.
          </p>
        )}

        <form onSubmit={onSubmit} className={SETTINGS_FORM}>
          <label className={SETTINGS_FIELD}>
            <span className={SETTINGS_LABEL}>Tenant ID</span>
            <input
              type="text"
              className="font-normal tracking-normal w-full"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
              autoComplete="off"
            />
          </label>

          <label className={SETTINGS_FIELD}>
            <span className={SETTINGS_LABEL}>Client ID</span>
            <input
              type="text"
              className="font-normal tracking-normal w-full"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
              autoComplete="off"
            />
          </label>

          <fieldset className={SETTINGS_FIELD}>
            <legend className={SETTINGS_LABEL}>Client secret</legend>
            <div className={FORM_ROW}>
              <label>
                <input
                  className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-0 ml-[5px]"
                  type="radio"
                  name="secret-mode"
                  checked={secretMode === "keep"}
                  onChange={() => setSecretMode("keep")}
                />{" "}
                Keep current
                {state.client_secret_set ? " (already set)" : " (none)"}
              </label>
              <label>
                <input
                  className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-0 ml-[5px]"
                  type="radio"
                  name="secret-mode"
                  checked={secretMode === "set"}
                  onChange={() => setSecretMode("set")}
                />{" "}
                Set new
              </label>
              <label>
                <input
                  className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-0 ml-[5px]"
                  type="radio"
                  name="secret-mode"
                  checked={secretMode === "clear"}
                  onChange={() => setSecretMode("clear")}
                />{" "}
                Clear
              </label>
            </div>
            {secretMode === "set" && (
              <input
                type="password"
                className="font-normal tracking-normal w-full"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder="paste the new client secret"
                autoComplete="off"
              />
            )}
          </fieldset>

          <div className={FORM_ROW}>
            <Button
              variant="primary"
              type="submit"
              disabled={submitting}
            >
              {submitting ? "Saving…" : "Save"}
            </Button>
            {state.source === "db" && (
              <Button
                type="button"
                onClick={onClear}
                disabled={submitting}
              >
                Delete saved credentials
              </Button>
            )}
          </div>
          {error && <p className="my-[1em] text-error-primary">Error: {error}</p>}
        </form>
      </SectionCard>
    </>
  );
}
