import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { linkButtonClass, pillClass } from "@/components/chips";
import {
  api,
  isEntraPlace,
  type CapabilityIdentity,
  type CapabilityLevel,
  type PrincipalMember,
  type ScopeCapabilities,
  type ScopeNode,
} from "@/api/client";
import { SectionCard } from "@/components/PageTemplate";
import {
  BADGE,
  BADGE_LEVEL,
  CAPTION,
  CARD,
  EMPTY_STATE,
  FILTER_BAR,
  TAG,
  TAG_TONE,
  WHO_ACCENT,
} from "@/components/classes";
import { cx } from "@/components/classes";
import { grantHref, originText } from "@/pages/grants/grantUtils";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";

import { LEVEL_SHORT, scopeHref, treeLabel } from "./scopeUtils";
import {
  conditionNote,
  decidingGrant,
  foldApps,
  howText,
  LEVEL_INFO,
  levelCounts,
  matchesFilter,
  pimSentence,
  powerText,
  ownersNote,
  statusTag,
  summarySentence,
  type KindFilter,
} from "./whoUtils";

const KINDS: { key: KindFilter; label: string }[] = [
  { key: "all", label: "Everyone" },
  { key: "person", label: "People" },
  { key: "group", label: "Groups" },
  { key: "app", label: "Apps" },
];
// Open by default: the levels a reviewer must look at.
const OPEN_BY_DEFAULT: CapabilityLevel[] = ["access", "keys", "unknown"];
const INSIDE_PAGE = 8;

const MUTED_CAPTION = cx(CAPTION, "text-quaternary");
/** A level's card at the top: its colour comes from WHO_ACCENT. */
const LEVEL_CARD =
  "flex min-h-11 cursor-pointer flex-col gap-0.5 rounded-[8px] border border-secondary bg-primary px-4 py-2 text-left text-primary [font:inherit] " +
  "hover:border-(--who-accent,var(--border))";
const LEVEL_HEAD =
  "grid min-h-11 w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-0.5 bg-transparent p-4 text-left text-inherit [border:0] [font:inherit] " +
  "disabled:cursor-default disabled:opacity-60";
const LEVEL_TITLE =
  "flex items-center gap-2 font-semibold " +
  "before:size-2.5 before:flex-none before:rounded-full before:bg-(--who-accent) before:content-['']";
const TWISTY =
  "col-[2] row-[1/span_2] mx-1 my-0 size-2 self-center border-r-2 border-b-2 border-r-(--text-secondary) border-b-(--text-secondary) [transition:transform_120ms_ease]";
/** Identities in a level, or places inside. */
const WHO_LIST = "m-0 list-none px-4 pt-0 pb-2";
const INSIDE_LIST = "m-0 list-none p-0";
/** One identity: avatar, what they are and can do, a status tag. */
const WHO_ROW =
  "grid grid-cols-[28px_minmax(0,1fr)_auto] items-start gap-2 border-t border-t-(--border) px-0 py-2 " +
  "max-[720px]:grid-cols-[28px_minmax(0,1fr)]";
const INSIDE_ROW = "grid grid-cols-[auto_minmax(0,1fr)] items-center gap-2 border-t border-t-(--border) px-0 py-2";
const AVATAR = "flex size-7 items-center justify-center rounded-full text-[0.75rem] font-semibold";
const AVATAR_TONE: Record<string, string> = {
  person: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  group: "bg-(--accent-purple-subtle) text-(--accent-purple)",
  app: "bg-primary_hover text-tertiary",
  other: "bg-primary_hover text-tertiary",
};
const WHO_MAIN = "flex min-w-0 flex-col gap-0.5";
const WHO_NAME = "font-medium [overflow-wrap:anywhere]";
const WHO_HOW = "text-[0.8125rem] text-tertiary [overflow-wrap:anywhere]";
const STATUS_TAG =
  "rounded-[4px] px-2 py-0.5 text-[0.75rem] whitespace-nowrap max-[720px]:col-[2] max-[720px]:justify-self-start";
const STATUS_TAG_TONE: Record<string, string> = {
  good: "bg-(--accent-green-subtle) text-success-primary",
  warn: "bg-(--accent-orange-subtle) text-warning-primary",
  muted: "bg-primary_hover text-tertiary",
};

/**
 * "Who can do what" — everyone whose access applies at this scope, once,
 * at the most powerful thing they can do, in plain words. Levels come
 * from GET /scopes/capabilities (app/core/capabilities.py).
 */
export default function ScopeWhoTab({ scope }: { scope: ScopeNode }) {
  const [data, setData] = useState<ScopeCapabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<CapabilityLevel>>(new Set(OPEN_BY_DEFAULT));
  const [insideAll, setInsideAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    api.scopes
      .capabilities(scope.id)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [scope.id]);

  const filtering = kind !== "all" || query.trim() !== "";
  const levels = useMemo(
    () =>
      (data?.levels ?? []).map((g) => ({
        ...g,
        visible: g.identities.filter((i) => matchesFilter(i, kind, query)),
      })),
    [data, kind, query],
  );

  if (error) return <p className="my-[1em] text-error-primary">Could not load who can do what: {error}</p>;
  if (!data) return <p className="my-[1em] text-quaternary">Working out who can do what…</p>;

  // Short name ("rg-app", not "prod / rg-app"): the breadcrumb gives the parent.
  const name = treeLabel(scope.id, scope.name, scope.level);
  const entra = isEntraPlace(scope.id);
  const pim = pimSentence(data.pim);
  const accessGroup = data.levels.find((l) => l.level === "access");
  // "Can change who has access" means now; on-request ones are said separately.
  const accessOnRequest = accessGroup?.on_request ?? 0;
  const accessCount = (accessGroup?.identities.length ?? 0) - accessOnRequest;

  function toggle(level: CapabilityLevel) {
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(level)) n.delete(level);
      else n.add(level);
      return n;
    });
  }

  function jump(level: CapabilityLevel) {
    setOpen((s) => new Set(s).add(level));
    requestAnimationFrame(() =>
      document.getElementById(`who-${level}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  return (
    <section className="grid gap-4">
      <p className="m-0 text-[0.9375rem] leading-[1.6]">
        {summarySentence(data, name)}{" "}
        {accessCount > 0 && (
          <span className="text-error-primary">
            {accessCount === 1 ? "1 of them can" : `${accessCount} of them can`} change who has access
            {accessOnRequest > 0 ? `, and ${accessOnRequest} more on request.` : "."}
          </span>
        )}
        {data.groups_unresolved > 0 && (
          <span className="text-quaternary">
            {" "}
            Members of {data.groups_unresolved} group{data.groups_unresolved === 1 ? "" : "s"} are unknown.
          </span>
        )}
      </p>
      {pim && <p className={cx(CAPTION, "mx-0 -mt-2 mb-0", pim.tone === "warn" ? "text-error-primary" : "text-quaternary")} title={data.pim.error ?? undefined}>{pim.text}</p>}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(160px,100%),1fr))] gap-2">
        {data.levels.map((g) => (
          <button
            key={g.level}
            type="button"
            className={cx("tracking-normal", LEVEL_CARD, WHO_ACCENT[g.level])}
            onClick={() => jump(g.level)}
            aria-label={`${LEVEL_INFO[g.level].title}: ${g.identities.length}`}
          >
            <span className="text-[0.8125rem] font-medium text-(--who-accent,var(--text-secondary))">{LEVEL_INFO[g.level].title}</span>
            <span className="text-[1.375rem] font-semibold">
              {g.identities.length}
              {g.on_request > 0 && <span className="text-[0.75rem] font-medium text-warning-primary"> incl. {g.on_request} on request</span>}
            </span>
            <span className={MUTED_CAPTION}>{levelCounts(g)}</span>
          </button>
        ))}
      </div>

      <div className={cx(FILTER_BAR, "m-0")}>
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            className={pillClass(kind === k.key)}
            aria-pressed={kind === k.key}
            onClick={() => setKind(k.key)}
          >
            {k.label}
          </button>
        ))}
        <input
          type="search"
          className="font-normal tracking-normal max-w-[360px] flex-[1_1_240px]"
          placeholder="Find a person, group, app or role"
          aria-label="Find a person, group, app or role"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {levels.map((g) => {
        const isOpen = filtering ? g.visible.length > 0 : open.has(g.level);
        const { shown, folded } = foldApps(g.visible);
        return (
          <div key={g.level} id={`who-${g.level}`} className={cx(CARD, "scroll-mt-4 overflow-hidden p-0", WHO_ACCENT[g.level])}>
            <button
              type="button"
              className={cx("tracking-normal", LEVEL_HEAD)}
              aria-expanded={isOpen}
              onClick={() => toggle(g.level)}
              disabled={filtering && g.visible.length === 0}
            >
              <span className={LEVEL_TITLE}>
                {LEVEL_INFO[g.level].title}{" "}
                <span className="text-quaternary">
                  · {filtering ? `${g.visible.length} of ${g.identities.length}` : g.identities.length}
                </span>
              </span>
              <span className={cx(MUTED_CAPTION, "col-[1]")}>{LEVEL_INFO[g.level].explain}</span>
              <span aria-hidden="true" className={cx(TWISTY, isOpen ? "[transform:rotate(45deg)]" : "[transform:rotate(-45deg)]")} />
            </button>
            {isOpen &&
              (g.visible.length === 0 ? (
                <p className={cx(EMPTY_STATE, "mx-4 mt-0 mb-4")}>Nobody at this level.</p>
              ) : (
                <ul className={WHO_LIST}>
                  {shown.map((i) => (
                    <IdentityRow key={i.id} identity={i} entra={entra} />
                  ))}
                  {folded.length > 0 && <FoldedApps apps={folded} entra={entra} />}
                </ul>
              ))}
          </div>
        );
      })}

      <SectionCard
        icon="places"
        title={`Inside ${name}`}
        hint="Resource groups and resources below where someone has more than they have here."
      >
        {data.inside_total === 0 ? (
          <p className={cx("my-[1em]", MUTED_CAPTION)}>
            Nothing inside this place gives anyone more than what's listed above.
          </p>
        ) : (
          <>
            <p className={cx("my-[1em]", MUTED_CAPTION)}>
              {data.inside_total} resource group{data.inside_total === 1 ? "" : "s"} and resources below give
              someone more than they have here.
            </p>
            <ul className={INSIDE_LIST}>
              {(insideAll ? data.inside : data.inside.slice(0, INSIDE_PAGE)).map((s) => (
                <li key={s.scope_id} className={INSIDE_ROW}>
                  <span className={cx(BADGE, BADGE_LEVEL[s.level])}>{LEVEL_SHORT[s.level]}</span>
                  <div className={WHO_MAIN}>
                    <Link to={scopeHref(s.scope_id, "who")} className={WHO_NAME} title={s.scope_id}>
                      {treeLabel(s.scope_id, s.name, s.level)}
                    </Link>
                    <span className={WHO_HOW}>
                      {s.extra_identities} more {s.extra_identities === 1 ? "identity" : "identities"} · strongest:{" "}
                      {LEVEL_INFO[s.top_level].title.toLowerCase()}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {data.inside.length > INSIDE_PAGE && (
              <button type="button" className={linkButtonClass()} onClick={() => setInsideAll((v) => !v)}>
                {insideAll ? "Show fewer" : `Show all ${data.inside.length}`}
              </button>
            )}
            {data.inside_total > data.inside.length && insideAll && (
              <p className={cx("my-[1em]", MUTED_CAPTION)}>
                Showing the {data.inside.length} with the most access; open a resource group to see the rest.
              </p>
            )}
          </>
        )}
      </SectionCard>
    </section>
  );
}

function IdentityRow({ identity: i, entra = false }: { identity: CapabilityIdentity; entra?: boolean }) {
  const [members, setMembers] = useState<PrincipalMember[] | null>(null);
  const [showMembers, setShowMembers] = useState(false);
  const tag = statusTag(i, new Date(), entra);
  const power = powerText(i);
  const condition = conditionNote(i);
  const grant = decidingGrant(i);
  const origin = grant ? originText(grant) : null;

  function toggleMembers() {
    setShowMembers((v) => !v);
    if (members === null) {
      api.principals
        .detail(i.id)
        .then((d) => setMembers(d.members ?? []))
        .catch(() => setMembers([]));
    }
  }

  return (
    <li className={WHO_ROW}>
      <span className={cx(AVATAR, AVATAR_TONE[i.kind])} aria-hidden="true">
        {i.kind === "person" ? "P" : i.kind === "group" ? "G" : "A"}
      </span>
      <div className={WHO_MAIN}>
        <span>
          <Link to={principalHref(i.id)} className={WHO_NAME}>
            {i.name ?? i.id}
          </Link>
          {i.kind === "group" && i.member_count ? (
            <span className="text-quaternary"> · {i.member_count} members</span>
          ) : null}
          {ownersNote(i) && <span className="text-quaternary"> · {ownersNote(i)}</span>}
          {!i.name && <span className={cx(TAG, TAG_TONE.unresolved)}>unresolved</span>}
        </span>
        <span className={WHO_HOW}>
          {howText(i)}
          {grant?.grant_id && (
            <>
              {" · "}
              <Link to={grantHref(grant.grant_id)}>details</Link>
            </>
          )}
        </span>
        {origin && <span className="text-[0.8125rem] text-tertiary [overflow-wrap:break-word]">{origin}</span>}
        {power && <span className="text-[0.8125rem] text-(--who-accent) [overflow-wrap:anywhere]">{power}</span>}
        {condition && (
          <span className={cx("text-[0.8125rem] [overflow-wrap:anywhere]", condition.unknown ? "text-warning-primary" : "text-success-primary")}>
            Limited by a condition: {condition.text}
          </span>
        )}
        {i.kind === "group" && !!i.member_count && (
          <button type="button" className={cx(linkButtonClass(), "self-start")} onClick={toggleMembers}>
            {showMembers ? "Hide people" : "Show people"}
          </button>
        )}
        {showMembers && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[0.8125rem]">
            {members === null ? (
              <span className={MUTED_CAPTION}>Loading members…</span>
            ) : (
              members.slice(0, 20).map((m) => (
                <Link key={m.member_id} to={principalHref(m.member_id)}>
                  {m.member_name ?? m.member_id}
                  <span className="text-quaternary"> · {typeLabel(m.member_type)}</span>
                </Link>
              ))
            )}
            {members && members.length > 20 && (
              <Link to={principalHref(i.id, "groups")} className={CAPTION}>
                All {members.length} members →
              </Link>
            )}
          </div>
        )}
      </div>
      {tag && <span className={cx(STATUS_TAG, STATUS_TAG_TONE[tag.tone])}>{tag.text}</span>}
    </li>
  );
}

function FoldedApps({ apps, entra = false }: { apps: CapabilityIdentity[]; entra?: boolean }) {
  const [open, setOpen] = useState(false);
  const names = apps
    .slice(0, 3)
    .map((a) => a.name ?? a.id)
    .join(", ");
  if (open) {
    return (
      <>
        {apps.map((a) => (
          <IdentityRow key={a.id} identity={a} entra={entra} />
        ))}
        <li className={WHO_ROW}>
          <button type="button" className={cx(linkButtonClass(), "col-[1/-1] justify-self-start")} onClick={() => setOpen(false)}>
            Fold apps
          </button>
        </li>
      </>
    );
  }
  return (
    <li className={WHO_ROW}>
      <span className={cx(AVATAR, AVATAR_TONE.app)} aria-hidden="true">
        A
      </span>
      <div className={WHO_MAIN}>
        <span className={WHO_NAME}>{apps.length} apps</span>
        <span className={WHO_HOW}>
          {names}
          {apps.length > 3 ? ` and ${apps.length - 3} more` : ""}
        </span>
      </div>
      <button type="button" className={linkButtonClass()} onClick={() => setOpen(true)}>
        Show apps
      </button>
    </li>
  );
}
