import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import Button from "@/components/Button";
import type { JobSchedule, JobScheduleUpdate } from "@/api/client";
import { BADGE, BADGE_TONE, CAPTION, EMPTY_STATE, FORM_FIELD, FORM_ROW, MONO, TABLE_COMPACT, TABLE_SCROLL, cx } from "@/components/classes";
import { whenText } from "@/components/FreshnessLine";
import { StatePill } from "@/components/PageTemplate";
import { formatDateTime } from "@/pages/scopes/scopeUtils";

import {
  editPatch,
  EVERY_PRESETS,
  KIND_LABEL,
  pausedText,
  pauseQuestion,
  relativeTime,
  scheduleLabel,
  targetDisplay,
  targetHref,
} from "./jobUtils";

const LOOKBACK_DAYS = [1, 7, 30, 90];

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");
/** A row's buttons, kept on one line at the right. */
export const ROW_ACTIONS = cx(FORM_ROW, "flex-nowrap justify-end");
/** Where a job runs: long ids wrap anywhere. */
export const CELL_TARGET = "min-w-50 max-w-105 [overflow-wrap:anywhere]";
/** The "More" menu under a row's button. */
const ROW_MENU_ITEMS =
  "absolute top-[calc(100%_+_4px)] right-0 z-5 flex min-w-[150px] flex-col rounded-[8px] border border-secondary bg-primary p-1 [box-shadow:0_6px_18px_rgba(15,23,42,0.12)]";
const ROW_MENU_ITEM =
  "cursor-pointer rounded-[4px] bg-transparent [border:none] bg-none px-2.5 py-1.5 text-left whitespace-nowrap text-primary [font:inherit] hover:bg-primary_hover focus-visible:bg-primary_hover";

type Props = {
  /** In display order; ``null`` while loading. */
  schedules: JobSchedule[] | null;
  names: ReadonlyMap<string, string>;
  /** The time zone the daily hour is in, e.g. "Europe/Zurich". */
  timezone: string;
  onRun: (s: JobSchedule) => void;
  onSetEnabled: (s: JobSchedule, enabled: boolean) => void;
  onSave: (s: JobSchedule, patch: JobScheduleUpdate) => Promise<boolean>;
  onDelete: (s: JobSchedule) => void;
};

/**
 * Every schedule: what, where, how often, next and last run. One main button per row
 * ("Run now"); Edit, Pause and Delete sit behind "More", and Pause asks first.
 */
export default function SchedulesTable({ schedules, names, timezone, onRun, onSetEnabled, onSave, onDelete }: Props) {
  // At most one row is open at a time: its menu, its edit form or its pause question.
  const [open, setOpen] = useState<{ id: number; what: "menu" | "edit" | "pause" | "delete" } | null>(null);
  const isOpen = (s: JobSchedule, what: "menu" | "edit" | "pause" | "delete") => open?.id === s.id && open.what === what;

  useEffect(() => {
    if (open?.what !== "menu") return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [open]);

  if (schedules !== null && schedules.length === 0) {
    return (
      <p className={cx("my-[1em]", EMPTY_STATE)}>No schedules yet. The daily check and hourly collection are set up after the first check.</p>
    );
  }
  return (
    <div className={TABLE_SCROLL}>
      <table className={TABLE_COMPACT}>
        <thead>
          <tr>
            <th scope="col">State</th>
            <th scope="col">What</th>
            <th scope="col">Where</th>
            <th scope="col">Repeats</th>
            <th scope="col">Next run</th>
            <th scope="col">Last run</th>
            <th scope="col" aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {schedules === null && (
            <tr>
              <td colSpan={7} className="text-quaternary">
                Loading…
              </td>
            </tr>
          )}
          {(schedules ?? []).map((s) => {
            const t = targetDisplay(s.kind, s.target, names);
            if (isOpen(s, "pause") || isOpen(s, "delete")) {
              const pausing = isOpen(s, "pause");
              return (
                <tr key={s.id}>
                  <td colSpan={7}>
                    <div className="mt-4 flex flex-wrap items-center gap-2" role="group" aria-label={pausing ? "Confirm pause" : "Confirm delete"}>
                      <span>
                        {pausing
                          ? pauseQuestion(s.kind, t.primary)
                          : `Delete the ${KIND_LABEL[s.kind].toLowerCase()} schedule for ${t.primary}? What it collected stays; it is not set up again by itself.`}
                      </span>
                      <Button
                        variant="primary"
                        size="sm"
                        type="button"
                        onClick={() => {
                          setOpen(null);
                          if (pausing) onSetEnabled(s, false);
                          else onDelete(s);
                        }}
                      >
                        {pausing ? "Pause" : "Delete"}
                      </Button>
                      <Button size="sm" type="button" onClick={() => setOpen(null)}>
                        Cancel
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            }
            if (isOpen(s, "edit")) {
              return (
                <tr key={s.id}>
                  <td colSpan={7}>
                    <ScheduleEdit
                      schedule={s}
                      name={t.primary}
                      timezone={timezone}
                      onCancel={() => setOpen(null)}
                      onSave={async (patch) => {
                        if (await onSave(s, patch)) setOpen(null);
                      }}
                    />
                  </td>
                </tr>
              );
            }
            return (
              <tr key={s.id} className={s.enabled ? undefined : "[&>td]:text-tertiary"}>
                <td>
                  {!s.enabled ? (
                    <StatePill tone="warn">Paused</StatePill>
                  ) : (s.failures ?? 0) > 0 ? (
                    <StatePill tone="danger">Failing</StatePill>
                  ) : (
                    <StatePill tone="ok">On</StatePill>
                  )}
                </td>
                <td>{KIND_LABEL[s.kind]}</td>
                <td className={CELL_TARGET}>
                  <Link to={targetHref(s.kind, s.target)} title={s.target}>
                    {t.primary}
                  </Link>
                  {t.secondary && (
                    <div className={cx(MONO, CAPTION_MUTED)} title={t.secondary}>
                      {t.secondary}
                    </div>
                  )}
                </td>
                <td>
                  {scheduleLabel(s)}
                  {s.at_hour != null && <div className={CAPTION_MUTED}>{timezone}</div>}
                </td>
                <td title={s.enabled ? formatDateTime(s.next_run_at) : undefined}>
                  {s.enabled ? (
                    <>
                      {relativeTime(s.next_run_at)}
                      {(s.failures ?? 0) > 0 && <span className={CAPTION_MUTED}> · retry</span>}
                    </>
                  ) : (
                    <span className={CAPTION_MUTED}>{s.paused_at ? pausedText(s, whenText) : "—"}</span>
                  )}
                </td>
                <td>
                  {s.last_run_at ? (
                    <>
                      <span title={formatDateTime(s.last_run_at)}>{relativeTime(s.last_run_at)}</span>{" "}
                      {s.last_job_status && <span className={cx(BADGE, BADGE_TONE[s.last_job_status])}>{s.last_job_status}</span>}
                    </>
                  ) : (
                    <span className="text-quaternary">never</span>
                  )}
                  {(s.failures ?? 0) > 1 && <div className={cx(CAPTION, "text-error-primary")}>Failed {s.failures} times in a row</div>}
                  <ErrorText summary={s.last_error_summary} raw={s.last_error} />
                </td>
                <td>
                  <div className={ROW_ACTIONS}>
                    {s.enabled ? (
                      <Button size="sm" type="button" onClick={() => onRun(s)}>
                        Run now
                      </Button>
                    ) : (
                      <Button variant="primary" size="sm" type="button" onClick={() => onSetEnabled(s, true)}>
                        Resume
                      </Button>
                    )}
                    <div className="relative">
                      <Button
                        size="sm"
                        type="button"
                        aria-haspopup="menu"
                        aria-expanded={isOpen(s, "menu")}
                        aria-label={`More actions for ${t.primary}`}
                        onClick={() => setOpen(isOpen(s, "menu") ? null : { id: s.id, what: "menu" })}
                      >
                        More
                      </Button>
                      {isOpen(s, "menu") && (
                        <div className={ROW_MENU_ITEMS} role="menu">
                          <button type="button" className={cx("tracking-normal", ROW_MENU_ITEM)} role="menuitem" onClick={() => setOpen({ id: s.id, what: "edit" })}>
                            Edit
                          </button>
                          {s.enabled ? (
                            <button type="button" className={cx("tracking-normal", ROW_MENU_ITEM)} role="menuitem" onClick={() => setOpen({ id: s.id, what: "pause" })}>
                              Pause
                            </button>
                          ) : (
                            <button
                              type="button"
                              className={cx("tracking-normal", ROW_MENU_ITEM)}
                              role="menuitem"
                              onClick={() => {
                                setOpen(null);
                                onRun(s);
                              }}
                            >
                              Run once now
                            </button>
                          )}
                          <button type="button" className={cx("tracking-normal", ROW_MENU_ITEM)} role="menuitem" onClick={() => setOpen({ id: s.id, what: "delete" })}>
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A failure in plain words, with the technical text one click away. */
export function ErrorText({ summary, raw }: { summary?: string | null; raw: string | null }) {
  if (!raw) return null;
  return (
    <div className={cx(CAPTION, "text-error-primary")}>
      {summary ?? raw}
      {summary && (
        <details className="mt-0.5">
          <summary className="cursor-pointer text-tertiary">Details</summary>
          <pre className="mx-0 mt-1 mb-0 max-h-40 max-w-[560px] overflow-auto rounded-[4px] bg-primary_hover px-2 py-1.5 text-[0.75rem] whitespace-pre-wrap text-tertiary [overflow-wrap:anywhere]">{raw}</pre>
        </details>
      )}
    </div>
  );
}

function ScheduleEdit({
  schedule: s,
  name,
  timezone,
  onSave,
  onCancel,
}: {
  schedule: JobSchedule;
  name: string;
  timezone: string;
  onSave: (patch: JobScheduleUpdate) => void;
  onCancel: () => void;
}) {
  const [everyHours, setEveryHours] = useState(s.every_hours);
  const [atHour, setAtHour] = useState<number | null>(s.at_hour ?? null);
  const [lookbackDays, setLookbackDays] = useState(s.initial_window_days);
  const presets = EVERY_PRESETS.some((p) => p.hours === s.every_hours)
    ? EVERY_PRESETS
    : [...EVERY_PRESETS, { hours: s.every_hours, label: `Every ${s.every_hours} hours` }];
  // The look-back only matters before a collection has ever succeeded.
  const firstRun = s.kind === "activity_pull" && !s.covered_until;
  return (
    <form
      className="flex flex-wrap gap-x-4 gap-y-2 [align-items:end]"
      aria-label={`Edit the schedule for ${name}`}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(editPatch(s, { everyHours, atHour, lookbackDays }));
      }}
    >
      <strong className="flex-[1_1_100%]">
        {KIND_LABEL[s.kind]} · {name}
      </strong>
      <label className={FORM_FIELD}>
        <span>Repeats</span>
        <select className="font-normal tracking-normal" value={everyHours} onChange={(e) => setEveryHours(Number(e.target.value))}>
          {presets.map((p) => (
            <option key={p.hours} value={p.hours}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {everyHours === 24 && (
        <label className={FORM_FIELD}>
          <span>At ({timezone})</span>
          <select className="font-normal tracking-normal" value={atHour ?? ""} onChange={(e) => setAtHour(e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">No fixed hour</option>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, "0")}:00
              </option>
            ))}
          </select>
        </label>
      )}
      {firstRun && (
        <label className={FORM_FIELD}>
          <span>First run looks back</span>
          <select className="font-normal tracking-normal" value={lookbackDays} onChange={(e) => setLookbackDays(Number(e.target.value))}>
            {LOOKBACK_DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 1 ? "1 day" : `${d} days`}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className={FORM_ROW}>
        <Button variant="primary" size="sm" type="submit">
          Save
        </Button>
        <Button size="sm" type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
