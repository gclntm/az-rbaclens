import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type AuthSettingsRead } from "@/api/client";
import { CAPTION, FORM_FIELD, FORM_ROW, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** Ids are typed in the mono font. */
const FIELD_TEXT = "font-mono text-[0.8125rem]";

export default function AuthTab() {
  const { showToast } = useToast();
  const [state, setState] = useState<AuthSettingsRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [enabled, setEnabled] = useState(false);
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [allowedGroups, setAllowedGroups] = useState("");
  const [adminGroups, setAdminGroups] = useState("");

  async function refresh() {
    setError(null);
    try {
      const cur = await api.settings.getAuth();
      setState(cur);
      setEnabled(cur.enabled);
      setTenantId(cur.tenant_id ?? "");
      setClientId(cur.client_id ?? "");
      setAllowedGroups(cur.allowed_group_ids.join(", "));
      setAdminGroups(cur.admin_group_ids.join(", "));
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
      const allowed = allowedGroups
        .split(",")
        .map((g) => g.trim())
        .filter(Boolean);
      const admins = adminGroups
        .split(",")
        .map((g) => g.trim())
        .filter(Boolean);
      const updated = await api.settings.putAuth({
        enabled,
        tenant_id: tenantId.trim() || null,
        client_id: clientId.trim() || null,
        allowed_group_ids: allowed,
        admin_group_ids: admins,
      });
      setState(updated);
      showToast(
        enabled
          ? "Auth enabled — reload the page to sign in"
          : "Auth settings saved",
        "success",
      );
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg);
      showToast(`Save failed: ${msg}`, "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SectionCard
      icon="shield"
      title="Sign-in (Microsoft Entra ID)"
    >
      <p className="my-[1em] text-quaternary">
        Optional. When enabled, every API call requires a valid Azure AD
        access token issued for the configured app registration. Disable
        for dev / test — the app keeps working without any sign-in.
      </p>

      {state?.updated_at && (
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          Last saved {new Date(state.updated_at).toLocaleString()}
        </p>
      )}
      {error && <p className="my-[1em] text-error-primary">{error}</p>}

      <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-4">
        <label className="inline-flex cursor-pointer flex-wrap items-center gap-2 select-none">
          <input
            className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-[3px] ml-1"
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>Require sign-in for every API call</span>
        </label>

        <label className={FORM_FIELD}>
          <span>Tenant ID</span>
          <input
            type="text"
            className={cx("font-normal tracking-normal", FIELD_TEXT)}
            placeholder="00000000-0000-0000-0000-000000000000"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            required={enabled}
          />
        </label>

        <label className={FORM_FIELD}>
          <span>Application (client) ID</span>
          <input
            type="text"
            className={cx("font-normal tracking-normal", FIELD_TEXT)}
            placeholder="00000000-0000-0000-0000-000000000000"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            required={enabled}
          />
        </label>

        <label className={FORM_FIELD}>
          <span>Allowed group IDs (optional, comma-separated)</span>
          <input
            type="text"
            className={cx("font-normal tracking-normal", FIELD_TEXT)}
            placeholder="group-guid-1, group-guid-2"
            value={allowedGroups}
            onChange={(e) => setAllowedGroups(e.target.value)}
          />
          <span className={CAPTION_MUTED}>
            When set, the access token must include at least one of these
            Entra group object IDs in its <code>groups</code> claim.
            Leave empty to accept any user from the tenant.
          </span>
        </label>

        <label className={FORM_FIELD}>
          <span>Admin group IDs (optional, comma-separated)</span>
          <input
            type="text"
            className={cx("font-normal tracking-normal", FIELD_TEXT)}
            placeholder="admin-group-guid"
            value={adminGroups}
            onChange={(e) => setAdminGroups(e.target.value)}
          />
          <span className={CAPTION_MUTED}>
            Members of these groups can access <strong>Settings</strong> and{" "}
            <strong>Database</strong>. Everyone else gets the read-only IAM
            Access view. Leave empty to grant admin to every signed-in user
            (single-team default).
          </span>
        </label>

        <div className={FORM_ROW}>
          <Button
            variant="primary"
            type="submit"
            disabled={submitting}
          >
            {submitting ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>

      <details className="mt-4 border-t border-t-secondary pt-4">
        <summary className="cursor-pointer text-[0.875rem] text-tertiary">How to register the Azure AD app</summary>
        <ol className="mt-2 mr-0 mb-0 ml-4 list-decimal pl-10 text-[0.875rem] leading-[1.6]">
          <li>
            In the Entra admin center → <strong>App registrations</strong> →{" "}
            <strong>New registration</strong>.
          </li>
          <li>
            <strong>Account types</strong>: single tenant (or the policy
            your org requires).
          </li>
          <li>
            <strong>Redirect URI</strong>: pick "Single-page application
            (SPA)" and set it to the exact origin of this app, e.g.{" "}
            <code>https://rbaclens.example.com</code> (no trailing slash).
          </li>
          <li>
            After creation, copy the <strong>Application (client) ID</strong>{" "}
            and <strong>Directory (tenant) ID</strong> into the fields
            above.
          </li>
          <li>
            Under <strong>API permissions</strong> add{" "}
            <code>User.Read</code> (delegated) — that's enough to issue
            tokens. The backend only validates the signature + audience;
            it doesn't call Graph on the user's behalf.
          </li>
          <li>
            (Optional) To restrict access by group, under{" "}
            <strong>Token configuration</strong> add a{" "}
            <strong>groups</strong> claim (Group ID, Access token), then
            paste the allowed group object IDs above.
          </li>
          <li>
            Save the settings here with <em>Require sign-in</em> ticked.
            Reload the page — you'll be redirected to login.microsoft.com.
          </li>
        </ol>
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          See <code>docs/auth.md</code> for the full walkthrough, including
          how to expose the same app's API surface and how to gate the
          settings page itself behind a separate group.
        </p>
      </details>
    </SectionCard>
  );
}
