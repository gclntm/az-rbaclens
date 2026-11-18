import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, type RoleDefinitionDetail } from "@/api/client";
import { BADGE, BADGE_TONE, CAPTION, cx, MONO } from "@/components/classes";
import Drawer, { DRAWER_META, DRAWER_SECTION, DRAWER_SECTION_TITLE } from "@/components/Drawer";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** A small solid flag beside a name ("R": a powerful role). */
const ROW_FLAG_RISK =
  "ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-[2px] px-1 align-[1px] text-[0.6875rem] leading-none font-semibold tracking-normal " +
  "bg-(--accent-red) text-(--on-accent)";
/** A button that reads as a link, inline in text or a table cell. */
const ROLE_LINK = "m-0 cursor-pointer [background:none] [border:none] p-0 text-left text-(--accent-blue) [font:inherit] hover:underline";

/**
 * App-wide role-detail drawer.
 *
 * Mount {@link RoleDetailProvider} once at the App root, then any
 * descendant can call ``useRoleDetail().open(roleId)`` to show the
 * drawer (passing either a bare role-definition GUID or a full ARM
 * id — the backend keys on the GUID either way). The component is
 * deliberately *not* per-page state because the role-name link
 * appears in three different pages (Scan view, Inspector,
 * Principals) and we want one source of truth.
 *
 * The drawer fetches lazily on open and caches in-memory by GUID for
 * the session: a reviewer toggling back and forth between the same
 * role on different rows doesn't re-hit the API.
 */

type Ctx = { open: (roleId: string) => void };
const RoleDetailContext = createContext<Ctx | null>(null);

export function useRoleDetail(): Ctx {
  const ctx = useContext(RoleDetailContext);
  if (!ctx) {
    throw new Error("useRoleDetail must be used within a RoleDetailProvider");
  }
  return ctx;
}

function guidOf(roleId: string): string {
  // ARM ids end with /<guid>; bare GUIDs pass through. Trailing slashes
  // are stripped just in case.
  return roleId.replace(/\/$/, "").split("/").pop()?.toLowerCase() ?? roleId;
}

export function RoleDetailProvider({ children }: { children: React.ReactNode }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RoleDefinitionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cache] = useState(() => new Map<string, RoleDefinitionDetail>());

  const open = useCallback((roleId: string) => {
    setActiveId(roleId);
  }, []);
  const close = useCallback(() => {
    setActiveId(null);
    setError(null);
    setDetail(null);
  }, []);

  useEffect(() => {
    if (!activeId) return;
    const guid = guidOf(activeId);
    const hit = cache.get(guid);
    if (hit) {
      setDetail(hit);
      setError(null);
      return;
    }
    setLoading(true);
    setDetail(null);
    setError(null);
    api.iam
      .getRoleDefinition(guid)
      .then((d) => {
        cache.set(guid, d);
        setDetail(d);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [activeId, cache]);

  const ctx = useMemo(() => ({ open }), [open]);

  return (
    <RoleDetailContext.Provider value={ctx}>
      {children}
      {activeId && (
        <Drawer title={detail?.role_name ?? (loading ? "Loading…" : "Role")} onClose={close}>
          <RoleDetailBody loading={loading} error={error} detail={detail} requestedId={activeId} />
        </Drawer>
      )}
    </RoleDetailContext.Provider>
  );
}

function RoleDetailBody({
  loading,
  error,
  detail,
  requestedId,
}: {
  loading: boolean;
  error: string | null;
  detail: RoleDefinitionDetail | null;
  requestedId: string;
}) {
  if (loading) return <p className="my-[1em] text-quaternary">Loading…</p>;
  if (error) {
    return (
      <div>
        <p className="my-[1em] text-error-primary">Could not load role definition.</p>
        <p className={cx("my-[1em]", CAPTION_MUTED)}>{error}</p>
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          Role definitions are populated by scans. If this role is from an
          older scan, re-run a scan that uses it.
        </p>
      </div>
    );
  }
  if (!detail) return null;

  const learnMoreHref =
    detail.role_type === "BuiltInRole"
      ? "https://learn.microsoft.com/azure/role-based-access-control/built-in-roles"
      : detail.role_type === "EntraBuiltInRole"
        ? "https://learn.microsoft.com/entra/identity/role-based-access-control/permissions-reference"
        : null;

  return (
    <>
      <dl className={DRAWER_META}>
        <div>
          <dt>Type</dt>
          <dd>
            <span
              className={cx(BADGE, (detail.role_type ?? "").endsWith("CustomRole") && BADGE_TONE.custom)}
            >
              {detail.role_type ?? "—"}
            </span>
          </dd>
        </div>
        <div>
          <dt>ID</dt>
          <dd className={cx(MONO, "text-quaternary")}>{detail.role_definition_id}</dd>
        </div>
      </dl>

      {detail.role_risk === "high" && (
        <div className="flex items-start gap-2 rounded-[4px] bg-(--accent-red-subtle) px-4 py-2 text-[0.8125rem] leading-[1.4] text-primary">
          <span className={ROW_FLAG_RISK}>R</span>
          <span>
            This is a powerful role: it can change resources widely or change who has access.
            Combined with a wide scope (management group / subscription),
            it warrants extra scrutiny in a review.
          </span>
        </div>
      )}

      {detail.description && (
        <p className="m-0 text-[0.875rem] text-tertiary">{detail.description}</p>
      )}

      <RoleActionsSection permissions={detail.permissions} />

      {detail.assignable_scopes && detail.assignable_scopes.length > 0 && (
        <section className={DRAWER_SECTION}>
          <h4 className={DRAWER_SECTION_TITLE}>Can be given on</h4>
          <ul className={cx(MONO, "m-0 list-disc pl-4 text-[0.8125rem] [&_li]:break-all")}>
            {detail.assignable_scopes.map((s) => (
              <li key={s} title={s}>
                {s}
              </li>
            ))}
          </ul>
        </section>
      )}

      {learnMoreHref && (
        <p className={cx("my-[1em]", CAPTION)}>
          <a href={learnMoreHref} target="_blank" rel="noreferrer">
            Microsoft built-in role reference ↗
          </a>
        </p>
      )}

      <p className={cx("mb-[1em]", CAPTION_MUTED, "mt-auto")}>
        Cached from Azure {new Date(detail.fetched_at).toLocaleString()}
      </p>

      {detail.role_definition_id !== guidOf(requestedId) && (
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          Requested {requestedId}, resolved to {detail.role_definition_id}.
        </p>
      )}
    </>
  );
}

function RoleActionsSection({
  permissions,
}: {
  permissions: RoleDefinitionDetail["permissions"];
}) {
  if (!permissions || permissions.length === 0) {
    return (
      <section className={DRAWER_SECTION}>
        <h4 className={DRAWER_SECTION_TITLE}>Permissions</h4>
        <p className="my-[1em] text-quaternary">No actions on file for this role.</p>
      </section>
    );
  }
  return (
    <section className={DRAWER_SECTION}>
      <h4 className={DRAWER_SECTION_TITLE}>Permissions</h4>
      {permissions.map((p, i) => (
        <div key={i} className="flex flex-col gap-2">
          <ActionList title="Actions" actions={p.actions} />
          <ActionList
            title="NotActions"
            actions={p.notActions}
            tone="muted"
          />
          <ActionList title="DataActions" actions={p.dataActions} />
          <ActionList
            title="NotDataActions"
            actions={p.notDataActions}
            tone="muted"
          />
        </div>
      ))}
    </section>
  );
}

function ActionList({
  title,
  actions,
  tone,
}: {
  title: string;
  actions: string[];
  tone?: "muted";
}) {
  if (actions.length === 0) return null;
  return (
    <div className="[&_li]:break-all [&_ul]:m-0 [&_ul]:pl-4 [&_ul]:text-[0.8125rem] [&_ul]:leading-[1.5]">
      <h5 className={cx("mx-0 mt-0 mb-1 text-[0.8125rem] font-semibold", tone === "muted" && "text-quaternary")}>
        {title} <span className={CAPTION_MUTED}>({actions.length})</span>
      </h5>
      <ul className={cx(MONO, "list-disc")}>
        {actions.map((a) => (
          <li key={a} title={a}>
            {a}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Click-to-open wrapper around a role name. Renders as a button styled
 * like an inline link so it sits naturally inside table cells.
 */
export function RoleLink({
  roleId,
  roleName,
  className,
}: {
  roleId: string | null | undefined;
  roleName: string | null | undefined;
  className?: string;
}) {
  const { open } = useRoleDetail();
  const label = roleName ?? "—";
  if (!roleId) {
    return <span className={className}>{label}</span>;
  }
  return (
    <button
      type="button"
      className={cx("tracking-normal", ROLE_LINK, className)}
      title={`${label} — click for permissions`}
      onClick={() => open(roleId)}
    >
      {label}
    </button>
  );
}
