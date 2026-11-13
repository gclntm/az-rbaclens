import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { pillClass } from "@/components/chips";
import { api, type ScopeNode } from "@/api/client";
import { PageHeader, SectionCard } from "@/components/PageTemplate";
import ScopeTree from "@/components/ScopeTree";
import { BADGE, BADGE_LEVEL, FILTER_BAR } from "@/components/classes";
import { cx } from "@/components/classes";
import { useMe } from "@/hooks/useMe";

import { LEVEL_SHORT, scopeHref, treeLabel } from "./scopeUtils";

/** Every place a finished check has walked, with what needs a look in each. */
export default function ScopesIndexPage() {
  const [tree, setTree] = useState<ScopeNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [includeResources, setIncludeResources] = useState(false);
  const me = useMe();
  const mine = me?.signed_in ? me.responsible_for : [];
  const [responsible, setResponsible] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    api.responsible
      .tree()
      .then((nodes) =>
        setResponsible(
          new Map(
            nodes
              .filter((n) => n.responsible && n.from_scope_id === n.scope_id)
              .map((n) => [n.scope_id, n.responsible?.person_name ?? n.responsible?.person_email ?? ""]),
          ),
        ),
      )
      .catch(() => setResponsible(new Map()));
  }, []);

  useEffect(() => {
    setTree(null);
    api.scopes
      .tree({ includeResources })
      .then(setTree)
      .catch((e: Error) => setError(e.message));
  }, [includeResources]);

  return (
    <>
      <PageHeader
        title="Places"
        sub={
          <>
            Every management group, subscription and resource group, and who can act on it. Access given
            on a place applies to everything inside it.
          </>
        }
      />

      {mine.length > 0 && (
        <SectionCard icon="places" title="Your places" hint="You are responsible for these, and everything inside them.">
          <div className={FILTER_BAR}>
            {mine.map((p) => (
              <Link key={p.scope_id} to={scopeHref(p.scope_id)} className={pillClass(false)}>
                <span className={cx(BADGE, BADGE_LEVEL[p.level], "p-0")}>{LEVEL_SHORT[p.level]}</span>{" "}
                {treeLabel(p.scope_id, p.name, p.level)}
              </Link>
            ))}
          </div>
        </SectionCard>
      )}

      <SectionCard
        icon="places"
        title="All places"
        hint="Open a level to see what is inside. Each place shows what needs a look there and who is responsible."
      >
        <div className={FILTER_BAR} style={{ marginBottom: "var(--space-md)" }}>
          <input
            type="search"
            className="font-normal tracking-normal max-w-[360px] flex-[1_1_240px]"
            placeholder="Find a place by name or id"
            aria-label="Find a place"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label className={pillClass(includeResources)}>
            <input
              type="checkbox"
              checked={includeResources}
              onChange={(e) => setIncludeResources(e.target.checked)}
            />
            Show single resources
          </label>
        </div>
        {error && <p className="my-[1em] text-error-primary">Could not load places: {error}</p>}
        {!tree && !error && <p className="my-[1em] text-quaternary">Loading…</p>}
        {tree && <ScopeTree nodes={tree} filter={filter} initialDepth={3} responsible={responsible} />}
      </SectionCard>
    </>
  );
}
