import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { linkButtonClass } from "@/components/chips";
import CloseButton from "@/components/CloseButton";
import Button from "@/components/Button";
import { api, isEntraPlace, type PrincipalPage, type ScopeAssignmentRow } from "@/api/client";
import { CAPTION, CELL_SUB, MONO, TAG, TAG_TONE } from "@/components/classes";
import { cx } from "@/components/classes";
import { useGroupMembers } from "@/components/GroupMembersDrawer";
import { RoleLink } from "@/components/RoleDetailDrawer";
import { useToast } from "@/components/Toast";
import { useDialogFocus } from "@/hooks/useDialogFocus";
import { grantHref, originText } from "@/pages/grants/grantUtils";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";

import {
  CATEGORY_LABEL,
  formatDateTime,
  isBroadHighRisk,
  LEVEL_LABEL,
  principalLabel,
  scopeHref,
  scopeLabel,
  SOURCE_LABEL,
  worstOpenSeverity,
} from "./scopeUtils";

/** A side sheet over the right edge, no backdrop: rows stay clickable to switch the selection. */
const DRAWER =
  "card fixed top-0 right-0 z-40 m-0 flex h-[100vh] w-[min(380px,100vw)] flex-col gap-4 overflow-y-auto " +
  "rounded-none border-0 bg-primary p-6 shadow-xl ring-1 ring-secondary outline-hidden " +
  "duration-300 animate-in slide-in-from-right motion-reduce:animate-none " +
  "print:mb-[12pt] print:break-inside-avoid print:border! print:border-[#888]! print:bg-white! print:[box-shadow:none]!";
const SECTION = "flex flex-col gap-1 text-[0.8125rem]";
const LABEL = "text-[0.6875rem] font-semibold tracking-[0.06em] text-tertiary uppercase";
const MUTED_CAPTION = cx(CAPTION, "text-quaternary");
const ORIGIN = "text-[0.8125rem] text-tertiary [overflow-wrap:break-word]";
/** A red box, or green when ``ok``. */
const callout = (ok: boolean) =>
  "flex flex-col gap-1 rounded-[4px] border px-4 py-2 text-[0.8125rem] " +
  (ok ? "border-(--accent-green) bg-(--accent-green-subtle)" : "border-(--accent-red) bg-(--accent-red-subtle)");

function quoteArg(value: string): string {
  return `"${value.replace(/(["\\$`])/g, "\\$1")}"`;
}

const ENTRA_ASSIGNMENTS = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments";

/** Remove an Entra role through Microsoft Graph: find the assignment's id, then delete it. */
export function entraRemoveCommand(principalId: string, roleDefinitionId: string): string {
  const role = roleDefinitionId.split("/").pop() ?? roleDefinitionId;
  const find = `${ENTRA_ASSIGNMENTS}?$filter=principalId eq '${principalId}' and roleDefinitionId eq '${role}'`;
  return `az rest --method DELETE --url "${ENTRA_ASSIGNMENTS}/$(az rest --method GET --url ${quoteArg(find)} --query "value[0].id" -o tsv)"`;
}

/** Side panel explaining one assignment: why it matters, usage, who/what it is. */
export default function AssignmentDrawer({
  row,
  onClose,
}: {
  row: ScopeAssignmentRow;
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const { open: openMembers } = useGroupMembers();
  const [principal, setPrincipal] = useState<PrincipalPage | null>(null);
  const [principalError, setPrincipalError] = useState<string | null>(null);

  // A panel beside the table, not over it: focus moves in and back, Tab is not held.
  const ref = useRef<HTMLElement>(null);
  useDialogFocus(ref, true, onClose, { trap: false });

  useEffect(() => {
    setPrincipal(null);
    setPrincipalError(null);
    api.principals
      .detail(row.principal_id)
      .then(setPrincipal)
      .catch((e: Error) => setPrincipalError(e.message));
  }, [row.principal_id]);

  const open = row.findings.filter((f) => f.status === "open");
  const worst = worstOpenSeverity(row);
  const risky = isBroadHighRisk(row);
  const subscriptions = principal?.summary.subscriptions ?? 0;

  function copyCli() {
    const cmd = isEntraPlace(row.scope)
      ? entraRemoveCommand(row.principal_id, row.role_definition_id)
      : [
          "az role assignment delete",
          `--assignee ${quoteArg(row.principal_id)}`,
          `--role ${quoteArg(row.role_name ?? row.role_definition_id)}`,
          `--scope ${quoteArg(row.scope)}`,
        ].join(" ");
    navigator.clipboard
      ?.writeText(cmd)
      .then(() => showToast("Removal command copied — review it before running", "success"))
      .catch(() => showToast("Could not copy", "error"));
  }

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      className={DRAWER}
      role="dialog"
      aria-modal="false"
      aria-label="Grant detail"
    >
      <div className="flex items-start justify-between gap-2">
        <div className={SECTION}>
          <span className={LABEL}>Grant</span>
          <span className="text-[1rem] font-semibold [overflow-wrap:anywhere]">
            {row.role_name ?? "Role"} → {principalLabel(row)}
          </span>
          <span className={MUTED_CAPTION}>
            on <Link to={scopeHref(row.scope)}>{scopeLabel(row.scope, row.scope_name)}</Link> (
            {LEVEL_LABEL[row.scope_level].toLowerCase()}) · {SOURCE_LABEL[row.source].toLowerCase()}
          </span>
          {originText(row) && <span className={ORIGIN}>{originText(row)}</span>}
          {row.grant_id && <Link to={grantHref(row.grant_id)}>Open the grant page →</Link>}
        </div>
        <CloseButton onClick={onClose} label="Close detail" />
      </div>

      {open.length > 0 ? (
        <div className={callout(worst === "low")}>
          <strong>
            {open.length} {open.length === 1 ? "item needs" : "items need"} a look
          </strong>
          <span>{open.map((f) => CATEGORY_LABEL[f.category] ?? f.category).join(" · ")}</span>
          <Link to={`${scopeHref(row.scope, "findings")}`}>Look at them →</Link>
        </div>
      ) : risky ? (
        <div className={callout(false)}>
          <strong>Powerful role given high up</strong>
          <span>Nothing open: it may already have been reviewed or accepted.</span>
        </div>
      ) : null}

      <div className={SECTION}>
        <span className={LABEL}>Changes made · 90 days</span>
        {row.coverage_pct === null ? (
          <span className="text-quaternary">
            Not worked out yet: the Activity Log for this subscription has not been collected.
          </span>
        ) : row.member_count === null && row.principal_type === "Group" ? (
          <span className="text-quaternary">
            Unknown: the group's members could not be read, so their changes cannot be matched.
          </span>
        ) : (
          <span>
            {row.coverage_pct} % of the role's {row.granted_pattern_count ?? "?"} permissions used for changes
            here · {row.observed_op_count ?? 0} kinds of change by this identity, anywhere. Reads are never
            recorded
            {row.covered_by_count > 0 &&
              ` · also covered by ${row.covered_by_count} broader grant${row.covered_by_count === 1 ? "" : "s"} above`}
          </span>
        )}
      </div>

      <div className={SECTION}>
        <span className={LABEL}>Who</span>
        <span>
          <Link to={principalHref(row.principal_id)}>
            <strong>{principalLabel(row)}</strong>
          </Link>{" "}
          · {typeLabel(row.principal_type)}
        </span>
        <span className={cx(MONO, CELL_SUB)}>{row.principal_id}</span>
        {principalError && <span className="text-quaternary">Could not load more about them.</span>}
        {principal && (
          <span className={MUTED_CAPTION}>
            {principal.principal.assignment_count} current grant
            {principal.principal.assignment_count === 1 ? "" : "s"} across {subscriptions}{" "}
            subscription{subscriptions === 1 ? "" : "s"} · member of {principal.principal.group_count}{" "}
            group{principal.principal.group_count === 1 ? "" : "s"} · last change{" "}
            {principal.principal.last_activity_at
              ? formatDateTime(principal.principal.last_activity_at)
              : "none recorded"}
          </span>
        )}
        {row.principal_type === "Group" && (
          <button
            type="button"
            className={linkButtonClass()}
            onClick={() =>
              openMembers({ scanId: row.scan_id, groupId: row.principal_id, groupName: row.principal_name })
            }
          >
            Show members{row.member_count !== null ? ` (${row.member_count})` : ""}
          </button>
        )}
      </div>

      <div className={SECTION}>
        <span className={LABEL}>Role</span>
        <span>
          <RoleLink roleId={row.role_definition_id} roleName={row.role_name} />{" "}
          {row.role_risk === "high" && <span className={cx(TAG, TAG_TONE.risk)}>R</span>}{" "}
          {(row.role_type ?? "").endsWith("CustomRole") && <span className={cx(TAG, TAG_TONE.custom)}>C</span>}
        </span>
        <span className={MUTED_CAPTION}>
          {(row.role_type ?? "").endsWith("CustomRole")
            ? isEntraPlace(row.scope) ? "Custom Entra role" : "Custom role"
            : (row.role_type ?? "").endsWith("BuiltInRole")
              ? isEntraPlace(row.scope) ? "Built-in Entra role" : "Built-in role"
              : "Unknown kind of role"}
        </span>
        {row.condition_text && (
          <span className={callout(row.condition_kind !== "other")}>
            <strong>Limited by a condition.</strong> {row.condition_text}.
          </span>
        )}
      </div>

      <div className={SECTION}>
        <Button size="sm" type="button" onClick={copyCli}>
          Copy removal command
        </Button>
        <span className={MUTED_CAPTION}>
          RBAC Lens never changes Azure. Review the command before running it.
        </span>
      </div>
    </aside>
  );
}
