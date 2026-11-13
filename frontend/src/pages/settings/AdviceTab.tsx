import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type AdviceSettingsRead, type RegistryPrincipal } from "@/api/client";
import { CAPTION, FORM_FIELD, FORM_ROW, MONO, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";
import { typeLabel } from "@/pages/principals/principalUtils";

import AiSettingsCard, { ADVICE_INPUT } from "./AiSettingsCard";

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** The break-glass identities, and the matches to add one. */
const BREAK_GLASS_LIST = "mx-0 my-2 grid list-none gap-1.5 p-0";
const BREAK_GLASS_ITEM = "flex flex-wrap items-center gap-2";

type Picked = { principal_id: string; name: string | null };

/**
 * The playbook's thresholds and break-glass identities (admins only).
 * Route: /settings/advice. The rules themselves are fixed (core/advice_texts.py).
 */
export default function AdviceTab() {
  const { showToast } = useToast();
  const [saved, setSaved] = useState<AdviceSettingsRead | null>(null);
  const [newDays, setNewDays] = useState(30);
  const [expiringDays, setExpiringDays] = useState(14);
  const [breakGlass, setBreakGlass] = useState<Picked[]>([]);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<RegistryPrincipal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function apply(s: AdviceSettingsRead) {
    setSaved(s);
    setNewDays(s.new_days);
    setExpiringDays(s.expiring_days);
    setBreakGlass(s.break_glass_names);
  }

  useEffect(() => {
    api.settings
      .getAdvice()
      .then(apply)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setMatches([]);
      return;
    }
    const t = setTimeout(() => {
      api.principals
        .list({ q, limit: 8 })
        .then(setMatches)
        .catch(() => setMatches([]));
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  function add(p: RegistryPrincipal) {
    if (!breakGlass.some((b) => b.principal_id === p.id)) {
      setBreakGlass([...breakGlass, { principal_id: p.id, name: p.display_name }]);
    }
    setQuery("");
    setMatches([]);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      apply(
        await api.settings.putAdvice({
          new_days: newDays,
          expiring_days: expiringDays,
          break_glass: breakGlass.map((b) => b.principal_id),
        }),
      );
      showToast("Advice settings saved", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="my-[1em] text-error-primary">Could not load the advice settings: {error}</p>;
  if (!saved) return <p className="my-[1em] text-quaternary">Loading…</p>;

  const dirty =
    newDays !== saved.new_days ||
    expiringDays !== saved.expiring_days ||
    breakGlass.map((b) => b.principal_id).join() !== saved.break_glass.join();

  return (
    <>
      <SectionCard
        icon="todo"
        title="Advice"
        hint="Every grant gets one action from a fixed playbook: the same facts always give the same advice, and each advice names its rule. These settings tune two thresholds and exempt emergency accounts."
      >
        <form onSubmit={save}>

          <div className={FORM_ROW}>
            <label className={FORM_FIELD}>
              <span>A grant counts as new for (days)</span>
              <input
                type="number"
                className={cx("font-normal tracking-normal", ADVICE_INPUT)}
                min={1}
                max={365}
                value={newDays}
                onChange={(e) => setNewDays(Number(e.target.value))}
                required
              />
            </label>
            <label className={FORM_FIELD}>
              <span>A grant ends soon within (days)</span>
              <input
                type="number"
                className={cx("font-normal tracking-normal", ADVICE_INPUT)}
                min={1}
                max={180}
                value={expiringDays}
                onChange={(e) => setExpiringDays(Number(e.target.value))}
                required
              />
            </label>
          </div>

          <h3 className="mx-0 mt-6 mb-1 text-[0.9375rem] font-bold">Break-glass identities</h3>
          <p className={cx("my-[1em]", CAPTION_MUTED)}>
            Emergency accounts that must keep standing power to change access. They are exempt from the rules
            &quot;make it PIM-eligible&quot; and &quot;remove: not used&quot;, and the report lists them.
          </p>
          {breakGlass.length === 0 ? (
            <p className={cx("my-[1em]", CAPTION)}>None.</p>
          ) : (
            <ul className={BREAK_GLASS_LIST}>
              {breakGlass.map((b) => (
                <li key={b.principal_id} className={BREAK_GLASS_ITEM}>
                  <span>{b.name ?? b.principal_id}</span>
                  <span className={cx(MONO, CAPTION_MUTED, "flex-[1_1_200px] [overflow-wrap:anywhere]")}>{b.principal_id}</span>
                  <Button
                    size="sm"
                    type="button"
                    onClick={() => setBreakGlass(breakGlass.filter((x) => x.principal_id !== b.principal_id))}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <label className={cx(FORM_FIELD, "max-w-105")}>
            <span>Add an identity</span>
            <input
              className="font-normal tracking-normal"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name, UPN or object id"
              aria-label="Find an identity to add"
            />
          </label>
          {matches.length > 0 && (
            <ul className={BREAK_GLASS_LIST} aria-label="Matching identities">
              {matches.map((m) => (
                <li key={m.id} className={BREAK_GLASS_ITEM}>
                  <Button size="sm" type="button" onClick={() => add(m)}>
                    Add
                  </Button>
                  <span>{m.display_name ?? m.id}</span>
                  <span className={CAPTION_MUTED}>{typeLabel(m.principal_type)}</span>
                </li>
              ))}
            </ul>
          )}

          <div className={FORM_ROW}>
            <Button variant="primary" size="sm" type="submit" disabled={busy || !dirty}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </SectionCard>
      <AiSettingsCard />
    </>
  );
}
