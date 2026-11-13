import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { copyButtonClass } from "@/components/chips";
import { BADGE, BADGE_LEVEL, CARD, MONO, TAG, TAG_TONE } from "@/components/classes";
import { api, type PrincipalPage as PrincipalPageData } from "@/api/client";
import WhatToDo from "@/components/advice/WhatToDo";
import { Breadcrumbs, PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { TabCount, TabNav, tabClass } from "@/components/TabNav";
import { useToast } from "@/components/Toast";
import { isRestricted, useMe } from "@/hooks/useMe";
import ChangesFeed from "@/pages/changes/ChangesFeed";
import { formatDateTime } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import PrincipalAccessTab from "./PrincipalAccessTab";
import PrincipalActivityTab from "./PrincipalActivityTab";
import PrincipalFindingsTab from "./PrincipalFindingsTab";
import PrincipalGroupsTab from "./PrincipalGroupsTab";
import { parsePrincipalTab, principalHref, typeLabel, type PrincipalTab } from "./principalUtils";

const TAB_LABEL: Record<PrincipalTab, string> = {
  access: "What they can do",
  groups: "Groups",
  activity: "What they did",
  findings: "Needs a look",
};

export default function PrincipalPage() {
  const { principalId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parsePrincipalTab(searchParams.get("tab"));
  const { showToast } = useToast();
  const [data, setData] = useState<PrincipalPageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const me = useMe();

  const load = useCallback(() => {
    api.principals
      .detail(principalId)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }, [principalId]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  function selectTab(next: PrincipalTab) {
    const sp = new URLSearchParams(searchParams);
    if (next === "access") sp.delete("tab");
    else sp.set("tab", next);
    setSearchParams(sp, { replace: true });
  }

  function copyId() {
    navigator.clipboard
      ?.writeText(principalId)
      .then(() => showToast("Object id copied", "success"))
      .catch(() => showToast("Could not copy", "error"));
  }

  if (error && !data) {
    return (
      <div className={CARD}>
        <p className="my-[1em] text-error-primary">
          {error.startsWith("404") ? "No daily check has seen this identity." : `Could not load this identity: ${error}`}
        </p>
        <Link to="/people">← People &amp; apps</Link>
      </div>
    );
  }
  if (!data) {
    return (
      <div className={CARD}>
        <p className="my-[1em] text-quaternary">Loading…</p>
      </div>
    );
  }

  const { principal: p, summary } = data;
  const isGroup = p.principal_type === "Group";
  // What someone did anywhere in Azure is shown to admins and to the person themselves.
  const tabs = (Object.keys(TAB_LABEL) as PrincipalTab[]).filter(
    (t) => t !== "activity" || !isRestricted(me) || me?.oid === p.id,
  );

  return (
    <>
      <Breadcrumbs items={[{ label: "People & apps", to: "/people" }, { label: p.display_name ?? p.id }]} />

      <PageHeader
        title={p.display_name ?? p.id}
        badges={
          <>
            <span className={cx(BADGE, BADGE_LEVEL.mgmt_group)}>{typeLabel(p.principal_type)}</span>
            {p.microsoft && (
              <span
                className={cx(BADGE, BADGE_LEVEL.resource)}
                title="An identity Microsoft runs for an Azure service (Defender for Cloud, AKS, backup…)"
              >
                Microsoft app
              </span>
            )}
            {p.sp_type === "ManagedIdentity" && <span className={cx(BADGE, BADGE_LEVEL.resource)}>Managed identity</span>}
            {!p.display_name && <span className={cx(TAG, TAG_TONE.unresolved)}>no longer in Entra ID?</span>}
          </>
        }
      >
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[0.8125rem] text-tertiary">
          <span className={cx(MONO, "[overflow-wrap:anywhere]")}>{p.id}</span>
          <button type="button" className={copyButtonClass()} onClick={copyId} aria-label="Copy the Entra object id">
            Copy
          </button>
          <span aria-hidden="true">·</span>
          <span>
            {isGroup
              ? `${data.members_total} member${data.members_total === 1 ? "" : "s"}`
              : `member of ${p.group_count} group${p.group_count === 1 ? "" : "s"}`}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            {p.last_activity_at
              ? `last change ${formatDateTime(p.last_activity_at)}`
              : "no change recorded (reads are not recorded)"}
          </span>
        </div>
      </PageHeader>

      <StatusTiles>
        <StatusTile
          tone="info"
          icon="shield"
          label="Grants"
          value={(summary.direct + summary.via_group).toLocaleString()}
          note={`${summary.direct} in their own name, ${summary.via_group} through ${data.groups.length} ${data.groups.length === 1 ? "group" : "groups"}; ${summary.subscriptions} ${summary.subscriptions === 1 ? "subscription" : "subscriptions"} reached`}
        />
        <StatusTile
          tone={summary.high_risk > 0 ? "danger" : "ok"}
          icon="roles"
          label="Powerful, given high up"
          value={summary.high_risk > 0 ? summary.high_risk.toLocaleString() : "None"}
          note="On a management group or a subscription"
        />
        <StatusTile
          tone={summary.open_findings > 0 ? "warn" : "ok"}
          icon="look"
          label="Needs a look"
          value={summary.open_findings > 0 ? `${summary.open_findings} open` : "Nothing open"}
          note="Open reasons on these grants"
        />
      </StatusTiles>

      {data.advice && <WhatToDo advice={data.advice} principalId={p.id} microsoft={!!p.microsoft} />}

      <TabNav label="Identity sections">
        {tabs.map((t) => (
          <a
            key={t}
            href={principalHref(p.id, t)}
            className={tabClass(tab === t)}
            aria-current={tab === t ? "page" : undefined}
            onClick={(e) => {
              e.preventDefault();
              selectTab(t);
            }}
          >
            {t === "groups" && isGroup ? "Members" : TAB_LABEL[t]}
            {t === "access" && <TabCount n={data.assignments.length} />}
            {t === "findings" && summary.open_findings > 0 && <TabCount n={summary.open_findings} problem />}
          </a>
        ))}
      </TabNav>

      {tab === "access" && (
        <>
          <PrincipalAccessTab data={data} />
          <SectionCard icon="changes" title="Changes to their access" hint="Grants given to or removed from them, newest first.">
            <ChangesFeed
              principal={p.id}
              initialDays={90}
              limit={20}
              emptyText="No change to their access in this period."
            />
          </SectionCard>
        </>
      )}
      {tab === "groups" && <PrincipalGroupsTab data={data} />}
      {tab === "activity" && tabs.includes("activity") && <PrincipalActivityTab principal={p} />}
      {tab === "findings" && <PrincipalFindingsTab findings={data.findings} onChanged={load} />}
    </>
  );
}
