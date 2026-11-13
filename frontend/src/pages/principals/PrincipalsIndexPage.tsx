import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { pillClass } from "@/components/chips";
import { api, type CapabilityLevel, type RegistryPrincipal } from "@/api/client";
import {
  CAPTION,
  CELL_PRINCIPAL,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  FILTER_BAR,
  NUM,
  TABLE_COMPACT,
  TABLE_FOOT,
  TABLE_SCROLL,
  TAG,
  TAG_TONE,
} from "@/components/classes";
import { PageHeader, SectionCard } from "@/components/PageTemplate";
import { formatDateTime } from "@/pages/scopes/scopeUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";

import { AM_CHIP, LEVEL_CHIP } from "./AccessMapView";
import { principalHref, typeLabel } from "./principalUtils";

const TYPES = ["User", "Group", "ServicePrincipal"] as const;
const LEVELS: CapabilityLevel[] = ["access", "keys", "change", "data", "view"];

function asLevel(v: string | null): CapabilityLevel | "" {
  return v && (LEVELS as string[]).includes(v) ? (v as CapabilityLevel) : "";
}
const LIMIT = 200;

/** Every principal any snapshot has seen, name-first; rows open the principal page. */
export default function PrincipalsIndexPage() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const type = sp.get("type") ?? "";
  const level = asLevel(sp.get("level"));
  const [query, setQuery] = useState(q);
  const [rows, setRows] = useState<RegistryPrincipal[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value);
    else next.delete(key);
    if (next.toString() !== sp.toString()) setSp(next, { replace: true });
  }

  // Debounce typing into the URL; the URL drives the fetch.
  useEffect(() => {
    const t = setTimeout(() => setParam("q", query.trim()), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the typed text drives this
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api.principals
      .list({ q: q || undefined, principal_type: type || undefined, level: level || undefined, limit: LIMIT })
      .then((r) => !cancelled && setRows(r))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [q, type, level]);

  return (
    <>
      <PageHeader
        title="People & apps"
        sub="Everyone and everything that holds access: people, groups and apps. Counts are current access only."
      />

      <SectionCard
        icon="people"
        title="Everyone with access"
        hint="Find someone by name, or narrow to a kind or to the strongest access they hold. Open one for their access, groups and activity."
      >
        <div className={FILTER_BAR}>
          <input
            type="search"
            className="font-normal tracking-normal max-w-[360px] flex-[1_1_240px]"
            placeholder="Name, UPN or object id"
            aria-label="Find a person, group or app"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {(["", ...TYPES] as string[]).map((t) => (
            <button
              key={t || "all"}
              type="button"
              className={pillClass(type === t)}
              aria-pressed={type === t}
              onClick={() => setParam("type", t)}
            >
              {t ? typeLabel(t) : "All"}
            </button>
          ))}
          <select
            className="font-normal tracking-normal max-w-full"
            aria-label="Strongest access"
            value={level}
            onChange={(e) => setParam("level", e.target.value)}
          >
            <option value="">Any access</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {LEVEL_INFO[l].title}
              </option>
            ))}
          </select>
        </div>
        {level && (
          <p className={cx(CAPTION, "text-quaternary", "mx-0 mt-2 mb-0")}>
            Identities whose strongest grant of their own is: <strong>{LEVEL_INFO[level].title.toLowerCase()}</strong>.
            Access through groups counts for the group.
          </p>
        )}

        {error && <p className="my-[1em] text-error-primary">{error}</p>}
        {rows !== null && rows.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>Nobody matches.</p>
        ) : (
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Who</th>
                  <th scope="col">Strongest</th>
                  <th scope="col" className={NUM}>Grants</th>
                  <th scope="col" className={NUM}>In groups</th>
                  <th scope="col">Last change</th>
                  <th scope="col">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {(rows ?? []).map((p) => (
                  <tr key={p.id}>
                    <td className={CELL_PRINCIPAL}>
                      <div className={CELL_STACK}>
                        <Link to={principalHref(p.id)} style={{ fontWeight: 500 }}>
                          {p.display_name ?? p.id}
                        </Link>
                        <span className={CELL_SUB}>
                          {typeLabel(p.principal_type)}
                          {!p.display_name && (
                            <>
                              {" "}
                              <span className={cx(TAG, TAG_TONE.unresolved)}>unresolved</span>
                            </>
                          )}
                        </span>
                      </div>
                    </td>
                    <td>
                      {p.level ? (
                        <span className={cx(AM_CHIP, LEVEL_CHIP[p.level])} title={LEVEL_INFO[p.level].explain}>
                          <span>{LEVEL_INFO[p.level].title}</span>
                        </span>
                      ) : (
                        <span className="text-quaternary">only through groups</span>
                      )}
                    </td>
                    <td className={NUM}>{p.assignment_count}</td>
                    <td className={NUM}>{p.group_count}</td>
                    <td>{p.last_activity_at ? formatDateTime(p.last_activity_at) : <span className="text-quaternary">none recorded</span>}</td>
                    <td>{formatDateTime(p.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows !== null && (
          <div className={TABLE_FOOT}>
            <span>
              {rows.length === LIMIT ? `First ${LIMIT} — refine the search to see more` : `${rows.length} shown`}
            </span>
          </div>
        )}
      </SectionCard>
    </>
  );
}
