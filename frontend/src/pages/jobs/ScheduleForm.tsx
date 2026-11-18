import { useEffect, useMemo, useState } from "react";

import Button from "@/components/Button";
import {
  api,
  ENTRA_AUDIT,
  ENTRA_AUDIT_NAME,
  type JobKind,
  MGMT_GROUPS,
  MGMT_GROUPS_NAME,
  type ScopeNode,
  type SubscriptionNode,
} from "@/api/client";
import { CAPTION, FORM_FIELD, FORM_ROW, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";

import { EVERY_PRESETS, KIND_LABEL, scopeOptions } from "./jobUtils";

const ALL_SUBS = "__all__";
const OTHER = "__other__";

/** Azure credential errors run to kilobytes; show the start, keep the rest in a tooltip. */
function shorten(message: string, max = 160): string {
  return message.length > max ? `${message.slice(0, max)}…` : message;
}
const LOOKBACK_DAYS = [1, 7, 30, 90] as const;

/** The form: What, Where, Repeat and look-back side by side, one column on a phone. */
const FORM_GRID =
  "mt-4 grid grid-cols-[minmax(140px,180px)_minmax(0,1fr)_minmax(140px,180px)_minmax(140px,180px)] gap-x-4 [align-items:end] gap-y-2 max-[720px]:grid-cols-[minmax(0,1fr)]";
/** A field's control fills its column, in the body font. */
const CONTROL = "w-full [font-family:var(--app-font-body)]";
/** A message under the form, across the whole width. */
const FORM_ERROR = "col-span-full m-0 whitespace-pre-line";

/**
 * New schedule, or the same job once. "Run once" replaces the old
 * manual pull form on the Activity page and the scope box on Snapshots.
 */
export default function ScheduleForm({
  subscriptions,
  subsLoading,
  subsError,
  onRefreshSubscriptions,
  onChanged,
}: {
  subscriptions: SubscriptionNode[];
  subsLoading: boolean;
  subsError: string | null;
  onRefreshSubscriptions: () => void;
  onChanged: () => void;
}) {
  const { showToast } = useToast();
  const [kind, setKind] = useState<JobKind>("activity_pull");
  const [scope, setScope] = useState("");
  const [place, setPlace] = useState("");
  const [tree, setTree] = useState<ScopeNode[]>([]);
  const [subscriptionId, setSubscriptionId] = useState("");
  // Collections are hourly by default, checks daily.
  const [everyHours, setEveryHours] = useState(1);
  const [lookbackDays, setLookbackDays] = useState(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.scopes
      .tree()
      .then(setTree)
      .catch(() => setTree([]));
  }, []);
  const places = useMemo(() => scopeOptions(tree, subscriptions), [tree, subscriptions]);
  // No known place yet: go straight to typing.
  const typing = place === OTHER || places.length === 0;

  const targets =
    kind === "snapshot"
      ? [typing ? scope.trim() : place].filter(Boolean)
      : subscriptionId === ALL_SUBS
        ? subscriptions.map((s) => s.id)
        : [subscriptionId].filter(Boolean);

  async function each(action: (target: string) => Promise<unknown>, verb: string) {
    setBusy(true);
    setError(null);
    const failures: string[] = [];
    for (const target of targets) {
      try {
        await action(target);
      } catch (e) {
        failures.push(`${target}: ${(e as Error).message}`);
      }
    }
    setBusy(false);
    const ok = targets.length - failures.length;
    if (failures.length) setError(failures.join("\n"));
    if (ok) {
      if (!failures.length) {
        setScope("");
        setPlace("");
      }
      showToast(`${verb} ${ok} ${KIND_LABEL[kind].toLowerCase()}${ok === 1 ? "" : "s"}`, "success");
      onChanged();
    }
  }

  function createSchedule(e: React.FormEvent) {
    e.preventDefault();
    void each(
      (target) =>
        api.jobs.createSchedule({
          kind,
          target,
          every_hours: everyHours,
          initial_window_days: lookbackDays,
        }),
      "Scheduled",
    );
  }

  function runOnce() {
    const end = new Date();
    const start = new Date(end.getTime() - lookbackDays * 86_400_000);
    void each(
      (target) =>
        kind === "snapshot"
          ? api.iam.startScan(target)
          : api.activity.startPull(target, start.toISOString(), end.toISOString()),
      "Started",
    );
  }

  return (
    <form className={FORM_GRID} onSubmit={createSchedule}>
      <label className={FORM_FIELD}>
        <span>What</span>
        <select
          className={cx("font-normal tracking-normal", CONTROL)}
          value={kind}
          onChange={(e) => {
            const next = e.target.value as JobKind;
            setKind(next);
            setEveryHours(next === "snapshot" ? 24 : 1);
          }}
        >
          <option value="activity_pull">{KIND_LABEL.activity_pull}</option>
          <option value="snapshot">{KIND_LABEL.snapshot}</option>
        </select>
      </label>

      {kind === "snapshot" ? (
        places.length > 0 ? (
          <label className={FORM_FIELD}>
            <span>Place</span>
            <select className={cx("font-normal tracking-normal", CONTROL)} value={place} onChange={(e) => setPlace(e.target.value)} required>
              <option value="" disabled>
                Select a place…
              </option>
              {places.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
              <option value={OTHER}>Other: type a GUID or path…</option>
            </select>
          </label>
        ) : (
          <ScopeInput value={scope} onChange={setScope} label="Place" />
        )
      ) : (
        <label className={FORM_FIELD}>
          <span>Subscription</span>
          <select
            className={cx("font-normal tracking-normal", CONTROL)}
            value={subscriptionId}
            onChange={(e) => setSubscriptionId(e.target.value)}
            disabled={subsLoading || subscriptions.length === 0}
            required
          >
            <option value="" disabled>
              {subsLoading
                ? "Loading…"
                : subscriptions.length === 0
                  ? "(no subscriptions cached)"
                  : "Select a subscription…"}
            </option>
            {subscriptions.length > 1 && (
              <option value={ALL_SUBS}>All subscriptions ({subscriptions.length})</option>
            )}
            {subscriptions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name} — {s.id}
              </option>
            ))}
            <option value={MGMT_GROUPS}>{MGMT_GROUPS_NAME} (what is done on the groups themselves)</option>
            <option value={ENTRA_AUDIT}>{ENTRA_AUDIT_NAME} (who adds or removes group members)</option>
          </select>
        </label>
      )}

      <label className={FORM_FIELD}>
        <span>Repeat</span>
        <select className={cx("font-normal tracking-normal", CONTROL)} value={everyHours} onChange={(e) => setEveryHours(Number(e.target.value))}>
          {EVERY_PRESETS.map((p) => (
            <option key={p.hours} value={p.hours}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {kind === "activity_pull" && (
        <label className={FORM_FIELD} title="Later runs only fetch what is new since the last collection that worked">
          <span>First run looks back</span>
          <select className={cx("font-normal tracking-normal", CONTROL)} value={lookbackDays} onChange={(e) => setLookbackDays(Number(e.target.value))}>
            {LOOKBACK_DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 1 ? "1 day" : `${d} days`}
                {d === 90 ? " (max)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      {kind === "snapshot" && places.length > 0 && place === OTHER && (
        <ScopeInput value={scope} onChange={setScope} label="GUID or path" wide autoFocus />
      )}

      <div className={cx(FORM_ROW, "col-span-full m-0")}>
        <Button variant="primary" size="sm" type="submit" disabled={busy || targets.length === 0}>
          Create schedule
        </Button>
        <Button
          size="sm"
          type="button"
          onClick={runOnce}
          disabled={busy || targets.length === 0}
          title={
            kind === "activity_pull"
              ? `Collect the last ${lookbackDays} days once, without a schedule`
              : "Check this place once now, without a schedule"
          }
        >
          Run once
        </Button>
        {kind === "activity_pull" && (
          <Button
            size="sm"
            type="button"
            onClick={onRefreshSubscriptions}
            disabled={subsLoading}
            title="Refresh the cached subscription list from Azure"
          >
            Refresh subscriptions
          </Button>
        )}
      </div>
      {kind === "activity_pull" && subsError && (
        <p className={cx(CAPTION, "text-error-primary", FORM_ERROR)} title={subsError}>
          Couldn't load subscriptions: {shorten(subsError)}
        </p>
      )}
      {error && (
        <p className={cx("text-error-primary", FORM_ERROR)} title={error}>
          {shorten(error, 400)}
        </p>
      )}
    </form>
  );
}

function ScopeInput({
  value,
  onChange,
  label,
  wide = false,
  autoFocus = false,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  wide?: boolean;
  autoFocus?: boolean;
}) {
  return (
    <label className={cx(FORM_FIELD, wide && "col-span-full")}>
      <span>{label}</span>
      <input
        type="text"
        className={cx("font-normal tracking-normal", CONTROL, "text-[0.8125rem]")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Management group, subscription GUID, <sub>/<rg> or ARM path"
        autoFocus={autoFocus}
        required
      />
    </label>
  );
}
