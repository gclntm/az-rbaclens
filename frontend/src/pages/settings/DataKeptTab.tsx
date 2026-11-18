import { useEffect, useState } from "react";

import { api, type DataKept } from "@/api/client";
import Button from "@/components/Button";
import { linkButtonClass } from "@/components/chips";
import { CAPTION, CARD, FORM_ROW, cx } from "@/components/classes";
import { useConfirm } from "@/components/ConfirmDialog";
import { SectionCard } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";

import {
  LINES,
  PREVIEW_WORDS,
  changedValues,
  checksEstimate,
  daysText,
  formatBytes,
  isValid,
  type DataLine,
} from "./dataKeptUtils";

const ROW =
  "grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.5fr)] items-start gap-4 border-t border-t-secondary py-3 max-[760px]:grid-cols-1";
const SUB = "mt-0.5 text-[0.8125rem] text-tertiary";
const NUMBER = "w-20 font-normal tracking-normal";
const FOLD_CHOICES = [3, 4, 7, 14, 30, 90];

/** Settings › Data kept: how long each kind of data is kept, how much is stored, a preview. */
export default function DataKeptTab() {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [data, setData] = useState<DataKept | null>(null);
  const [edited, setEdited] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, number> | null>(null);

  function load(d: DataKept) {
    setData(d);
    setEdited({ ...d.values });
    setPreview(null);
  }

  useEffect(() => {
    api.database
      .dataKept()
      .then(load)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (!data) {
    return (
      <div className={CARD}>
        <p className={cx("my-[1em]", error ? "text-error-primary" : "text-quaternary")}>{error ?? "Loading…"}</p>
      </div>
    );
  }

  const changes = changedValues(data, edited);
  const invalid = Object.entries(edited).filter(([name, value]) => !isValid(data, name, value));
  const set = (name: string, value: number) => {
    setEdited((e) => ({ ...e, [name]: value }));
    setPreview(null);
  };

  async function runPreview(): Promise<Record<string, number> | null> {
    setBusy("preview");
    try {
      const { deletes } = await api.database.previewDataKept(changes);
      setPreview(deletes);
      return deletes;
    } catch (e) {
      showToast(`Preview failed: ${(e as Error).message}`, "error");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    const deletes = await runPreview();
    if (!deletes) return;
    const lost = Object.entries(deletes).filter(([k, n]) => n > 0 && k !== "days_to_fold");
    if (lost.length) {
      const ok = await confirm({
        title: "Save these values?",
        body: `At its next run, retention will delete ${lost
          .map(([k, n]) => `${n.toLocaleString()} ${PREVIEW_WORDS[k] ?? k}`)
          .join(", ")}. Deleted data can't be restored.`,
        action: "Save",
        danger: true,
      });
      if (!ok) return;
    }
    setBusy("save");
    try {
      load(await api.database.saveDataKept(changes));
      showToast("Saved", "success");
    } catch (e) {
      showToast(`Couldn't save: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  async function applyNow() {
    const ok = await confirm({
      title: "Apply what is kept now?",
      body: "Retention runs now with the saved values. Deleted data can't be restored.",
      action: "Apply now",
      danger: true,
    });
    if (!ok) return;
    setBusy("apply");
    try {
      const r = await api.database.applyRetention();
      showToast(
        `Deleted ${r.events_deleted.toLocaleString()} events, ${r.snapshots_deleted + r.failed_snapshots_deleted} checks and ${r.audit_deleted} audit entries.`,
        "success",
      );
      load(await api.database.dataKept());
    } catch (e) {
      showToast(`Retention failed: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  const total = Object.values(data.stored).reduce((sum, s) => sum + (s.bytes ?? 0), 0);

  return (
    <SectionCard
      icon="history"
      title="Data kept"
      hint="How long RBAC Lens keeps each kind of data. Saved values override the server's defaults."
      actions={
        <span className={CAPTION}>
          {total ? `${formatBytes(total)} stored · ` : ""}
          {data.retention_interval_hours ? `retention runs every ${data.retention_interval_hours} h` : "automatic retention is off"}
        </span>
      }
    >
      <div className={cx(ROW, "border-t-0 py-1 text-[0.75rem] text-quaternary")} aria-hidden="true">
        <span>What</span>
        <span>Stored now</span>
        <span>Keep</span>
      </div>
      {LINES.map((line) => (
        <Line key={line.key} line={line} data={data} edited={edited} set={set} />
      ))}

      <div className="border-t border-t-secondary pt-3">
        <p className={cx(CAPTION, "my-0")}>
          Never deleted: the check a place shows now, and how far each log is collected.{" "}
          <a href="https://github.com/gclntm/az-rbaclens/blob/main/docs/iam-access.md#what-deleting-keeps" target="_blank" rel="noreferrer">
            What deleting keeps
          </a>
        </p>
        {invalid.length > 0 && (
          <p className="mt-2 mb-0 text-[0.8125rem] text-error-primary" role="alert">
            Check the highlighted values: {invalid.map(([name]) => name).join(", ")}.
          </p>
        )}
        {preview && (
          <p className={cx(CAPTION, "mt-2 mb-0")} role="status">
            {Object.entries(preview).some(([, n]) => n > 0)
              ? "With these values, retention would delete " +
                Object.entries(preview)
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => `${n.toLocaleString()} ${PREVIEW_WORDS[k] ?? k}`)
                  .join(", ") +
                "."
              : "With these values, retention would delete nothing."}
          </p>
        )}
        <div className={cx(FORM_ROW, "mt-3 justify-end")}>
          <Button type="button" onClick={applyNow} disabled={busy !== null}>
            {busy === "apply" ? "Applying…" : "Apply now"}
          </Button>
          <Button type="button" onClick={() => void runPreview()} disabled={busy !== null || invalid.length > 0}>
            {busy === "preview" ? "Counting…" : "Preview what would be deleted"}
          </Button>
          <Button
            variant="primary"
            type="button"
            onClick={() => void save()}
            disabled={busy !== null || invalid.length > 0 || Object.keys(changes).length === 0}
          >
            {busy === "save" ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </SectionCard>
  );
}

function Stored({ data, line }: { data: DataKept; line: DataLine }) {
  if (line.key === "compact_after_days") {
    return <span>{data.folded_until ? `folded up to ${new Date(data.folded_until).toLocaleDateString()}` : "nothing folded yet"}</span>;
  }
  const s = line.stored ? data.stored[line.stored] : null;
  if (!s) return null;
  return (
    <span>
      {s.count.toLocaleString()} {line.storedNoun}
      {s.oldest && (
        <>
          <br />
          oldest {new Date(s.oldest).toLocaleDateString()}
        </>
      )}
      {s.bytes != null && (
        <>
          <br />
          {formatBytes(s.bytes)}
        </>
      )}
    </span>
  );
}

function Days({
  name,
  data,
  edited,
  set,
  zero,
}: {
  name: string;
  data: DataKept;
  edited: Record<string, number>;
  set: (n: string, v: number) => void;
  /** What 0 means here, when not "kept forever". */
  zero?: string;
}) {
  const value = edited[name] ?? 0;
  const ok = isValid(data, name, value);
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <input
        className={cx(NUMBER, !ok && "outline outline-(--accent-red)")}
        type="number"
        min={0}
        step={1}
        aria-label={name}
        aria-invalid={!ok}
        value={value}
        onChange={(e) => set(name, Math.round(Number(e.target.value)))}
      />
      days
      <span className="text-quaternary">· {value === 0 && zero ? zero : daysText(value)}</span>
      {data.sources[name] === "saved" && value === data.values[name] && (
        <button type="button" className={linkButtonClass(true)} onClick={() => set(name, data.server[name])}>
          server's ({data.server[name] === 0 && zero ? zero : daysText(data.server[name])})
        </button>
      )}
    </span>
  );
}

function Line({ line, data, edited, set }: { line: DataLine; data: DataKept; edited: Record<string, number>; set: (n: string, v: number) => void }) {
  let control;
  if (line.key === "checks") {
    const days = edited.snapshot_keep_days ?? 0;
    const est = checksEstimate(data, days);
    control = (
      <span className="flex flex-col gap-1.5">
        <span className="flex flex-wrap items-center gap-1.5">
          every check for
          <Days name="snapshot_keep_days" data={data} edited={edited} set={set} zero="off: only the count below" />
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          and at least the last
          <input
            className={cx(NUMBER, !isValid(data, "snapshot_keep_per_scope", edited.snapshot_keep_per_scope ?? 0) && "outline outline-(--accent-red)")}
            type="number"
            min={1}
            step={1}
            aria-label="snapshot_keep_per_scope"
            value={edited.snapshot_keep_per_scope ?? 0}
            onChange={(e) => set("snapshot_keep_per_scope", Math.round(Number(e.target.value)))}
          />
          per place
        </span>
        {est && <span className={cx("text-[0.75rem]", est.large ? "text-warning-primary" : "text-tertiary")}>{est.text}</span>}
      </span>
    );
  } else if (line.key === "compact_after_days") {
    const value = edited.compact_after_days ?? 0;
    const choices = Array.from(new Set([...FOLD_CHOICES, value].filter((v) => v > 0))).sort((a, b) => a - b);
    control = (
      <select
        className="font-normal tracking-normal"
        aria-label="compact_after_days"
        value={value}
        onChange={(e) => set("compact_after_days", Number(e.target.value))}
      >
        {choices.map((d) => (
          <option key={d} value={d}>
            Fold after {d} days
          </option>
        ))}
        <option value={0}>Never fold</option>
      </select>
    );
  } else {
    control = <Days name={line.key} data={data} edited={edited} set={set} />;
  }
  return (
    <div className={ROW}>
      <div>
        <p className="m-0 text-[0.875rem] font-medium">{line.title}</p>
        <p className={SUB}>{line.what}</p>
        {line.key === "compact_after_days" && (
          <p className={SUB}>Raising it later doesn't unfold days already folded.</p>
        )}
      </div>
      <div className="text-[0.8125rem] text-tertiary">
        <Stored data={data} line={line} />
      </div>
      <div className="text-[0.8125rem]">{control}</div>
    </div>
  );
}
