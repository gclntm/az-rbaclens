import { Link } from "react-router-dom";

import type { PrincipalPage } from "@/api/client";
import {
  CELL_PRINCIPAL,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  MONO,
  NUM,
  TABLE_COMPACT,
  TABLE_FOOT,
  TABLE_SCROLL,
} from "@/components/classes";
import { SectionCard } from "@/components/PageTemplate";
import { cx } from "@/components/classes";

import { principalHref, typeLabel } from "./principalUtils";

/** Why a group shows no members: empty in Entra ID, or never read from Graph. */
export function emptyGroupText(read: boolean | undefined): string {
  return read
    ? "This group has no members in Entra ID, so nobody gets its access today. Anyone added to it later gets all of it at once."
    : "The members could not be read from Microsoft Graph. The app registration may need GroupMember.Read.All (or the Directory Readers role).";
}

/** Groups that give this principal access; for a group, also its members. */
export default function PrincipalGroupsTab({ data }: { data: PrincipalPage }) {
  const { members, members_total: total, members_read: read } = data;
  return (
    <>
      {members !== null && (
        <SectionCard
          icon="people"
          title="Members"
          hint="Everyone in the group, including through nested groups, from the newest daily check that could read it. Each of them has everything listed under “What they can do”."
        >
          {total === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>{emptyGroupText(read)}</p>
          ) : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE_COMPACT}>
                <thead>
                  <tr>
                    <th scope="col">Member</th>
                    <th scope="col">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <tr key={m.member_id}>
                      <td className={CELL_PRINCIPAL}>
                        <div className={CELL_STACK}>
                          <Link to={principalHref(m.member_id)}>{m.member_name ?? m.member_id}</Link>
                          {m.member_name && <span className={cx(MONO, CELL_SUB)}>{m.member_id}</span>}
                        </div>
                      </td>
                      <td>{typeLabel(m.member_type)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {total > members.length && (
            <div className={TABLE_FOOT}>
              <span>
                Showing {members.length} of {total}
              </span>
            </div>
          )}
        </SectionCard>
      )}

      <SectionCard
        icon="people"
        title={members !== null ? "Member of" : "Groups"}
        hint="Groups that hold access somewhere and include this identity, directly or through nested groups. Groups without any grant are not tracked."
      >
        {data.groups.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>Not a member of any group that holds access.</p>
        ) : (
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col" className={NUM}>Grants</th>
                  <th scope="col" className={NUM}>Powerful, high up</th>
                  <th scope="col">Members read in</th>
                </tr>
              </thead>
              <tbody>
                {data.groups.map((g) => (
                  <tr key={g.group_id}>
                    <td className={CELL_PRINCIPAL}>
                      <div className={CELL_STACK}>
                        <Link to={principalHref(g.group_id)}>{g.group_name ?? g.group_id}</Link>
                        <span className={cx(MONO, CELL_SUB)}>{g.group_id}</span>
                      </div>
                    </td>
                    <td className={NUM}>{g.assignment_count}</td>
                    <td className={NUM}>
                      {g.high_risk_count > 0 ? <span className="text-error-primary">{g.high_risk_count}</span> : 0}
                    </td>
                    <td>
                      <Link to={`/iam-access/${g.scan_id}`}>check #{g.scan_id}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </>
  );
}
