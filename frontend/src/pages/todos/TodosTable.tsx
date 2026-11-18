import { Link } from "react-router-dom";

import type { Todo } from "@/api/client";
import { CELL_PRINCIPAL, CELL_ROLE, CELL_STACK, CELL_SUB, TABLE_COMPACT, TABLE_SCROLL } from "@/components/classes";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { periodLabel } from "@/pages/reviews/reviewUtils";
import { scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { Badge, BadgeWithDot } from "@/ui/untitled/components/base/badges/badges";

import { dueWords, STATUS_LABEL } from "./todoUtils";

/**
 * The to-dos as one compact table. The cards view keeps the steps and commands.
 * A native table with the shared look (styles/redesign.css), like every other
 * table: Tab goes link by link (React Aria's table moved by arrow keys instead). Untitled UI badges in the cells.
 */
export default function TodosTable({ rows }: { rows: Todo[] }) {
  return (
    <div className={TABLE_SCROLL}>
      <table className={TABLE_COMPACT} aria-label="Changes to make in Azure">
        <thead>
          <tr>
            <th scope="col">Action</th>
            <th scope="col">Who</th>
            <th scope="col">Role</th>
            <th scope="col">Place</th>
            <th scope="col">Review</th>
            <th scope="col">Due</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>
                <Badge type="pill-color" size="sm" color={t.action === "remove" ? "error" : "warning"}>
                  {t.action === "remove" ? "Remove" : "Change"}
                </Badge>
              </td>
              <td className={CELL_PRINCIPAL}>
                <span className={CELL_STACK}>
                  <Link to={principalHref(t.principal_id)}>{t.principal_name ?? t.principal_id}</Link>
                  <span className={CELL_SUB}>{typeLabel(t.principal_type)}</span>
                </span>
              </td>
              <td className={CELL_ROLE}>
                {t.kind === "owner" ? `owner of ${t.detail?.object_name ?? t.detail?.object_id ?? "an app"}` : t.kind === "secret" ? `${t.detail?.kind ?? "secret"} of ${t.detail?.object_name ?? t.detail?.object_id ?? "an app"}` : (t.role_name ?? "—")}
              </td>
              <td>
                <Link to={scopeHref(t.scope)}>{scopeLabel(t.scope, t.scope_name)}</Link>
              </td>
              <td className="whitespace-nowrap">
                {t.period ? <Link to={`/reviews/${t.round_id}`}>{periodLabel(t.period)}</Link> : "—"}
              </td>
              <td className="whitespace-nowrap">
                {t.status === "open" ? (
                  <BadgeWithDot type="modern" size="sm" color={t.overdue ? "error" : "gray"}>
                    {dueWords(t)}
                  </BadgeWithDot>
                ) : (
                  <BadgeWithDot type="modern" size="sm" color={t.status === "done" ? "success" : "gray"}>
                    {STATUS_LABEL[t.status]}
                  </BadgeWithDot>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
