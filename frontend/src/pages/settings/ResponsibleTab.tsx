import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { pillClass } from "@/components/chips";
import Button from "@/components/Button";
import {
  api,
  type ResponsibleNode,
  type ResponsibleSuggestion,
  type ResponsibleSuggestions,
} from "@/api/client";
import {
  BADGE,
  BADGE_LEVEL,
  CAPTION,
  EMPTY_STATE,
  FILTER_BAR,
  FORM_FIELD,
  FORM_ROW,
  LEVEL_TAG,
  TABLE_COMPACT,
  TABLE_ROW_SELECTED,
  TABLE_SCROLL,
  cx,
} from "@/components/classes";
import { useToast } from "@/components/Toast";
import TreeLines from "@/components/tree/TreeLines";
import { treeGuides } from "@/components/tree/treeGuides";
import { SectionCard } from "@/components/PageTemplate";
import { useMe } from "@/hooks/useMe";
import { LEVEL_SHORT, scopeHref, treeLabel } from "@/pages/scopes/scopeUtils";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

/** The blocks of a settings tab, one under the other (Who is responsible, Notifications). */
export const TAB_STACK = "flex flex-col gap-6";

/** A list of items with their actions (suggestions, notifications), a line between them. */
export const FEED = "m-0 list-none p-0";
export const FEED_ITEM =
  "flex flex-wrap items-start gap-4 border-t border-t-secondary px-0 py-4 first:[border-top:0]";
export const FEED_MAIN = "flex min-w-0 flex-[1_1_240px] flex-col gap-0.5";
export const FEED_TITLE = "font-medium [overflow-wrap:anywhere]";
export const FEED_META = "text-[0.75rem] text-tertiary [overflow-wrap:anywhere]";
export const FEED_ACTIONS = "flex flex-[0_1_auto] flex-wrap gap-1";

/** A one-field form (the tag name, a test message): field and button on one line; not on paper. */
export const FIELD_FORM = cx(FORM_ROW, "items-end print:hidden!");
export const FIELD_FORM_FIELD = cx(FORM_FIELD, "flex-[0_1_320px]");
export const FIELD_FORM_INPUT =
  "min-h-10 rounded-[4px] border border-secondary bg-(--bg-input) px-2 py-1 text-primary [font:inherit]";

/** A search box in a filter bar or an inline form. */
const SEARCH_INPUT = "max-w-[360px] flex-[1_1_240px]";
const ROW_ACTIONS = cx(FORM_ROW, "flex-nowrap justify-end");

/**
 * Settings › Who is responsible. Admins set a person per place; it applies
 * to everything inside unless someone else is set lower down. Suggestions
 * come from a tag (``owner`` by default). Route: /settings/responsible
 * (``?place=<scope id>`` opens that place's editor).
 */
export default function ResponsibleTab() {
  const me = useMe();
  const admin = !!me?.is_admin;
  const { showToast } = useToast();
  const [params] = useSearchParams();
  const focus = params.get("place");
  const [tree, setTree] = useState<ResponsibleNode[] | null>(null);
  const [sugg, setSugg] = useState<ResponsibleSuggestions | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.responsible.tree(), api.responsible.suggestions()])
      .then(([t, s]) => {
        setTree(t);
        setSugg(s);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function act(action: () => Promise<unknown>, done: string): Promise<boolean> {
    try {
      await action();
      showToast(done, "success");
      load();
      return true;
    } catch (e) {
      showToast((e as Error).message, "error");
      return false;
    }
  }

  return (
    <section className={TAB_STACK}>
      <div className="rounded-[8px] border border-secondary bg-(--accent-blue-subtle) p-4 text-[0.75rem] leading-[1.5] text-tertiary">
        <strong className="text-primary">This is not the Azure Owner role.</strong> Being responsible for a place gives no access in Azure.
        It only decides who reviews the place. A person set on a place covers everything inside it, unless someone
        else is set lower down.
        {!admin && me && " Only admins can change who is responsible."}
      </div>

      {error && <p className="my-[1em] text-error-primary">Could not load: {error}</p>}

      {sugg && (
        <SuggestionsCard
          data={sugg}
          admin={admin}
          onUse={(s) => act(() => api.responsible.use(s.scope_id), `${s.person_name ?? "They"} now responsible for ${s.scope_name ?? s.scope_id}`)}
          onIgnore={(s) => act(() => api.responsible.ignore(s.scope_id), "Ignored")}
          onTagName={(name) => act(() => api.responsible.saveSettings(name), `Tag name saved. Reading "${name}" on every place…`)}
        />
      )}

      {tree && <TreeCard nodes={tree} admin={admin} focus={focus} act={act} />}
    </section>
  );
}

function SuggestionsCard({
  data,
  admin,
  onUse,
  onIgnore,
  onTagName,
}: {
  data: ResponsibleSuggestions;
  admin: boolean;
  onUse: (s: ResponsibleSuggestion) => void;
  onIgnore: (s: ResponsibleSuggestion) => void;
  onTagName: (name: string) => Promise<boolean>;
}) {
  const [name, setName] = useState(data.tag_name);
  useEffect(() => setName(data.tag_name), [data.tag_name]);

  function save(e: FormEvent) {
    e.preventDefault();
    if (name.trim() && name.trim() !== data.tag_name) void onTagName(name.trim());
  }

  return (
    <SectionCard
      icon="people"
      title="Suggestions from Azure tags"
      actions={
        <>
          <span className={CAPTION_MUTED}>
            {data.open.length} to decide · {data.unmatched.length} could not match
          </span>
        </>
      }
    >
      <form className={FIELD_FORM} onSubmit={save}>
        <label className={FIELD_FORM_FIELD}>
          <span>Tag read on subscriptions and resource groups</span>
          <input className={cx("tracking-normal", FIELD_FORM_INPUT)} value={name} onChange={(e) => setName(e.target.value)} disabled={!admin} maxLength={512} />
        </label>
        {admin && (
          <Button size="sm" type="submit" disabled={!name.trim() || name.trim() === data.tag_name}>
            Save
          </Button>
        )}
      </form>
      <p className={cx("my-[1em]", CAPTION_MUTED)}>
        When the tag holds the email of one Entra ID user, it becomes a suggestion here. Tags are read at each daily
        check.
      </p>

      {data.open.length === 0 && data.unmatched.length === 0 ? (
        <p className={cx("my-[1em]", EMPTY_STATE)}>Nothing to decide.</p>
      ) : (
        <ul className={FEED}>
          {data.open.map((s) => (
            <li key={s.scope_id} className={FEED_ITEM}>
              <span className={cx(BADGE, BADGE_LEVEL[s.level])}>{LEVEL_SHORT[s.level]}</span>
              <div className={FEED_MAIN}>
                <span className={FEED_TITLE}>
                  <Link to={scopeHref(s.scope_id)}>{treeLabel(s.scope_id, s.scope_name, s.level)}</Link>:{" "}
                  {s.person_name ?? s.person_email}
                </span>
                <span className={FEED_META}>
                  {data.tag_name} = {s.tag_value}
                  {s.current_name ? ` · now: ${s.current_name}` : " · nobody responsible yet"}
                </span>
              </div>
              {admin && (
                <div className={FEED_ACTIONS}>
                  <Button variant="primary" size="sm" type="button" onClick={() => onUse(s)}>
                    Use
                  </Button>
                  <Button size="sm" type="button" onClick={() => onIgnore(s)}>
                    Ignore
                  </Button>
                </div>
              )}
            </li>
          ))}
          {data.unmatched.map((s) => (
            <li key={s.scope_id} className={FEED_ITEM}>
              <span className={cx(BADGE, BADGE_LEVEL[s.level])}>{LEVEL_SHORT[s.level]}</span>
              <div className={FEED_MAIN}>
                <span className={FEED_TITLE}>
                  <Link to={scopeHref(s.scope_id)}>{treeLabel(s.scope_id, s.scope_name, s.level)}</Link>: could not
                  match
                </span>
                <span className={FEED_META}>
                  {data.tag_name} = {s.tag_value} · put the person&apos;s email address in the tag
                </span>
              </div>
              {admin && (
                <div className={FEED_ACTIONS}>
                  <Button size="sm" type="button" onClick={() => onIgnore(s)}>
                    Ignore
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {data.ignored.length > 0 && (
        <p className={cx("my-[1em]", CAPTION_MUTED)}>
          {data.ignored.length} ignored. They come back if the tag value changes.
        </p>
      )}
    </SectionCard>
  );
}

function TreeCard({
  nodes,
  admin,
  focus,
  act,
}: {
  nodes: ResponsibleNode[];
  admin: boolean;
  focus: string | null;
  act: (action: () => Promise<unknown>, done: string) => Promise<boolean>;
}) {
  const focused = focus ? nodes.find((n) => n.scope_id === focus.toLowerCase()) : undefined;
  const [showRgs, setShowRgs] = useState(focused?.level === "resource_group");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(focused && admin ? focused.scope_id : null);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return nodes.filter((n) => {
      if (!showRgs && n.level === "resource_group" && n.scope_id !== editing) return false;
      if (!q) return true;
      return (
        (n.name ?? n.scope_id).toLowerCase().includes(q) ||
        (n.responsible?.person_name ?? "").toLowerCase().includes(q) ||
        (n.responsible?.person_email ?? "").toLowerCase().includes(q)
      );
    });
  }, [nodes, showRgs, query, editing]);

  const guides = useMemo(() => treeGuides(visible), [visible]);
  const setHere = nodes.filter((n) => n.responsible && n.from_scope_id === n.scope_id).length;
  const nobody = nodes.filter((n) => n.level !== "resource_group" && !n.responsible).length;

  return (
    <SectionCard
      icon="people"
      title="Who is responsible"
      actions={
        <>
          <span className={CAPTION_MUTED}>
            set on {setHere} {setHere === 1 ? "place" : "places"}
            {nobody > 0 && ` · ${nobody} management groups and subscriptions with nobody (they go to the admins)`}
          </span>
        </>
      }
    >
      <div className={FILTER_BAR}>
        <input
          type="search"
          className={cx("font-normal tracking-normal", SEARCH_INPUT)}
          placeholder="Find a place or a person"
          aria-label="Find a place or a person"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className={pillClass(showRgs)}>
          <input type="checkbox" checked={showRgs} onChange={(e) => setShowRgs(e.target.checked)} />
          Show resource groups
        </label>
      </div>
      <div className={TABLE_SCROLL}>
        <table className={cx(TABLE_COMPACT, "[&_td:first-child]:h-10 [&_td:first-child]:py-0 [&_td:first-child]:whitespace-nowrap")}>
          <thead>
            <tr>
              <th scope="col">Place</th>
              <th scope="col">Responsible</th>
              <th scope="col">Where it comes from</th>
              {admin && <th scope="col" aria-label="actions" />}
            </tr>
          </thead>
          <tbody>
            {visible.map((n, i) => {
              const here = !!n.responsible && n.from_scope_id === n.scope_id;
              return (
                <tr key={n.scope_id} className={n.scope_id === focused?.scope_id ? TABLE_ROW_SELECTED : undefined}>
                  <td>
                    <span className="flex min-w-0 items-center gap-1.5 self-stretch">
                      {!query && <TreeLines {...guides[i]} />}
                      <span className={LEVEL_TAG}>{LEVEL_SHORT[n.level]}</span>
                      <Link to={scopeHref(n.scope_id)} className="overflow-hidden text-ellipsis whitespace-nowrap text-primary no-underline hover:text-(--accent-blue)" title={n.scope_id}>
                        {treeLabel(n.scope_id, n.name, n.level)}
                      </Link>
                    </span>
                  </td>
                  <td className={here ? undefined : "text-quaternary"}>
                    {editing === n.scope_id ? (
                      <PersonEditor
                        onCancel={() => setEditing(null)}
                        onSave={async (person) => {
                          if (await act(() => api.responsible.set(n.scope_id, person), "Saved")) setEditing(null);
                        }}
                      />
                    ) : n.responsible ? (
                      <span title={n.responsible.person_email ?? undefined}>
                        {n.responsible.person_name ?? n.responsible.person_email}
                      </span>
                    ) : (
                      "nobody"
                    )}
                  </td>
                  <td className={CAPTION_MUTED}>
                    {here
                      ? `set here${n.responsible?.source === "tag" ? " (from the tag)" : ""}`
                      : n.from_scope_id
                        ? `from ${n.from_scope_name ?? n.from_scope_id}`
                        : "goes to the admins"}
                  </td>
                  {admin && (
                    <td>
                      {editing !== n.scope_id && (
                        <div className={ROW_ACTIONS}>
                          <Button size="sm" type="button" onClick={() => setEditing(n.scope_id)}>
                            {here ? "Change" : "Set someone"}
                          </Button>
                          {here && (
                            <Button
                              size="sm"
                              type="button"
                              onClick={() => act(() => api.responsible.clear(n.scope_id), "Removed")}
                            >
                              Remove
                            </Button>
                          )}
                        </div>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

function PersonEditor({ onSave, onCancel }: { onSave: (person: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      className={cx(FORM_ROW, "print:hidden!")}
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSave(value.trim());
      }}
    >
      <input
        autoFocus
        className={cx("font-normal tracking-normal", SEARCH_INPUT)}
        placeholder="Email or Entra object id"
        aria-label="Email or Entra object id of the person"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        maxLength={320}
      />
      <Button variant="primary" size="sm" type="submit" disabled={!value.trim()}>
        Save
      </Button>
      <Button size="sm" type="button" onClick={onCancel}>
        Cancel
      </Button>
    </form>
  );
}
