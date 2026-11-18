import { Copy01 } from "@untitledui/icons";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { pillClass } from "@/components/chips";
import { CAPTION, CARD, EMPTY_STATE, FILTER_BAR, FORM_ROW } from "@/components/classes";
import { api, type Todo, type TodoStatus, type TodoSummary } from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import Commands from "@/components/advice/Commands";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { useMe } from "@/hooks/useMe";
import { formatDay, grantHref } from "@/pages/grants/grantUtils";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { periodLabel, subjectLine } from "@/pages/reviews/reviewUtils";
import { scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { Button } from "@/ui/untitled/components/base/buttons/button";
import { cx } from "@/components/classes";

import TodosTable from "./TodosTable";
import { dueWords, STATUS_LABEL } from "./todoUtils";

const STATUSES: (TodoStatus | "all")[] = ["open", "done", "dismissed", "all"];

type Accent = "must" | "should" | "info";
/** A to-do card: a block with a thick left edge in the advice's colour (late: red). */
const TODO_CARD = cx(CARD, "min-w-0 border-l-4 print:border-l! [&_p]:[overflow-wrap:anywhere]");
const TODO_EDGE: Record<Accent, string> = {
  must: "border-l-(--accent-red)",
  should: "border-l-(--accent-orange)",
  info: "border-l-(--accent-green)",
};
/** Remove / Change, as a small capitalised pill in the same colour. */
const WEIGHT = "flex-none rounded-full px-2 py-px text-[0.6875rem] font-semibold tracking-[0.03em] whitespace-nowrap uppercase";
const WEIGHT_TONE: Record<Accent, string> = {
  must: "bg-(--accent-red-subtle) text-error-primary",
  should: "bg-(--accent-orange-subtle) text-warning-primary",
  info: "bg-(--bg-input) text-tertiary",
};
const MUTED_CAPTION = cx(CAPTION, "text-quaternary");
/**
 * The reason typed for "won't do". The app's input look stays (the element
 * style wins on size, border and colour); it takes the row's width.
 */
const NOTE_INPUT = "flex-[1_1_240px] [font-style:inherit] [font-weight:inherit] [line-height:inherit]";

/**
 * The changes to make in Azure, decided in finished reviews.
 * Route: /todos (?round=<id> for one review). The daily check ticks them off.
 */
export default function TodosPage() {
  const me = useMe();
  const { showToast } = useToast();
  const [sp, setSp] = useSearchParams();
  const status = (sp.get("status") as TodoStatus | "all" | null) ?? "open";
  const show = sp.get("show") === "all" ? "all" : "mine";
  const roundId = Number(sp.get("round")) || undefined;
  const view = sp.get("view") === "table" ? "table" : "cards";
  const [rows, setRows] = useState<Todo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<TodoSummary | null>(null);
  const signedIn = !!me?.signed_in;

  const loadSummary = useCallback(() => {
    api.todos
      .summary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);
  useEffect(loadSummary, [loadSummary]);

  const load = useCallback(() => {
    api.todos
      .list({ status, show, roundId })
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    loadSummary();
  }, [status, show, roundId, loadSummary]);
  useEffect(load, [load]);

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value);
    else next.delete(key);
    setSp(next, { replace: true });
  }

  async function copyScript() {
    try {
      await navigator.clipboard.writeText(await api.todos.script(show));
      showToast("Script copied. Read it before running.", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  const overdue = rows?.filter((t) => t.overdue).length ?? 0;

  return (
    <>
      <PageHeader
        title="To do"
        sub={
          <>
            Changes decided in finished reviews: grants to remove, or to give another way. RBAC Lens never changes
            Azure; each line has the command to run, and the next daily check ticks it off.
          </>
        }
      />

      {summary && (
        <StatusTiles>
          <StatusTile
            tone={(signedIn ? summary.mine_open : summary.open) > 0 ? "warn" : "ok"}
            icon="todo"
            label={signedIn ? "Yours to make" : "To make in Azure"}
            value={`${(signedIn ? summary.mine_open : summary.open).toLocaleString()} open`}
            note={signedIn ? `${summary.open.toLocaleString()} open on your places in all` : "Removals and changes decided in reviews"}
          />
          <StatusTile
            tone={(signedIn ? summary.mine_overdue : summary.overdue) > 0 ? "danger" : "ok"}
            icon="calendar"
            label="Late"
            value={(signedIn ? summary.mine_overdue : summary.overdue) > 0 ? `${(signedIn ? summary.mine_overdue : summary.overdue).toLocaleString()} overdue` : "None"}
            note="Open more than 14 days after the review"
          />
        </StatusTiles>
      )}

      <SectionCard
        icon="todo"
        tone={overdue > 0 ? "warn" : "info"}
        title="Changes to make"
        hint="One card per grant: what was decided, then the steps and the command. The next daily check ticks it off."
        actions={
          <>
            <Button color="secondary" size="sm" iconLeading={Copy01} onClick={copyScript}>
              Copy removal script
            </Button>
            <DownloadLink href={api.todos.exportUrl(show)}>Export CSV</DownloadLink>
          </>
        }
      >
        <div className={FILTER_BAR}>
          {STATUSES.map((s) => (
            <button
              key={s}
              type="button"
              className={pillClass(status === s)}
              aria-pressed={status === s}
              onClick={() => setParam("status", s === "open" ? null : s)}
            >
              {s === "all" ? "All" : STATUS_LABEL[s]}
            </button>
          ))}
          {signedIn && (
            <button
              type="button"
              className={pillClass(show === "all")}
              aria-pressed={show === "all"}
              onClick={() => setParam("show", show === "all" ? null : "all")}
              title="Also the to-dos of other reviewers on your places"
            >
              Not only mine
            </button>
          )}
          {roundId && (
            <button type="button" className={pillClass(true)} onClick={() => setParam("round", null)}>
              One review only ✕
            </button>
          )}
          <span className="ml-auto flex gap-2">
            {(["cards", "table"] as const).map((v) => (
              <button
                key={v}
                type="button"
                className={pillClass(view === v)}
                aria-pressed={view === v}
                onClick={() => setParam("view", v === "cards" ? null : v)}
              >
                {v === "cards" ? "Cards" : "Table"}
              </button>
            ))}
          </span>
        </div>

        {error && <p className="my-[1em] text-error-primary">Could not load the to-dos: {error}</p>}
        {!rows && !error && <p className="my-[1em] text-quaternary">Loading…</p>}
        {rows && rows.length === 0 && (
          <p className={cx("my-[1em]", EMPTY_STATE)}>
            {status === "open"
              ? "Nothing to do. Finished reviews add a line here for each grant to remove or change."
              : "Nothing in this list."}
          </p>
        )}
        {rows && rows.length > 0 && view === "table" ? (
          <TodosTable rows={rows} />
        ) : (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            {rows?.map((t) => <TodoCard key={t.id} todo={t} onChanged={load} />)}
          </div>
        )}
      </SectionCard>
    </>
  );
}

function TodoCard({ todo: t, onChanged }: { todo: Todo; onChanged: () => void }) {
  const { showToast } = useToast();
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");

  async function dismiss(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.todos.dismiss(t.id, reason.trim());
      showToast("Dismissed", "success");
      onChanged();
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  const accent = t.status !== "open" ? "info" : t.overdue ? "must" : "should";
  return (
    <article className={cx(TODO_CARD, TODO_EDGE[accent])}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cx(WEIGHT, WEIGHT_TONE[accent])}>{t.action === "remove" ? "Remove" : "Change"}</span>
        <h2 className="m-0 min-w-0 flex-[1_1_240px] text-[0.9375rem] font-medium [overflow-wrap:anywhere]">
          <Link to={principalHref(t.principal_id)}>{t.principal_name ?? t.principal_id}</Link>{" "}
          <span className={MUTED_CAPTION}>{typeLabel(t.principal_type)}</span> · {subjectLine(t) ?? (t.role_name ?? "a role")} on{" "}
          <Link to={scopeHref(t.scope)}>{scopeLabel(t.scope, t.scope_name)}</Link>
        </h2>
        <span className={cx(CAPTION, t.overdue ? "text-error-primary" : "text-quaternary")}>{dueWords(t)}</span>
      </div>
      <p className={cx("my-[1em]", MUTED_CAPTION)}>
        {t.period ? (
          <Link to={`/reviews/${t.round_id}`}>{periodLabel(t.period)} review</Link>
        ) : (
          "Review"
        )}
        {t.decided_by_name && ` · decided by ${t.decided_by_name}`}
        {t.reviewer_name && ` · reviewer ${t.reviewer_name}`}
        {t.advice_rule && ` · advice ${t.advice_rule.split(" ")[0]}`} · <Link to={grantHref(t.grant_id)}>the grant</Link>
      </p>
      {t.note && <p className="my-[1em]">Note: {t.note}</p>}
      {t.status === "open" && (
        <>
          {t.how.length > 0 && (
            <ol className="mx-0 my-2 list-decimal pl-5">
              {t.how.map((step) => (
                <li key={step} className="[overflow-wrap:anywhere]">{step}</li>
              ))}
            </ol>
          )}
          <Commands commands={t.commands} />
        </>
      )}
      {t.status === "done" && (
        <p className={cx("my-[1em]", CAPTION)}>
          Done: the daily check of {t.done_at ? formatDay(t.done_at) : "—"} no longer saw the grant.
          {t.replacements.length > 0 && ` They now hold ${t.replacements.join(", ")} there; next month's review covers it.`}
        </p>
      )}
      {t.status === "dismissed" && (
        <p className={cx("my-[1em]", CAPTION)}>
          Dismissed{t.dismissed_by_name && ` by ${t.dismissed_by_name}`}: {t.dismissed_reason}
        </p>
      )}
      {t.status === "open" && t.action === "change" && t.replacements.length > 0 && (
        <p className={cx("my-[1em]", MUTED_CAPTION)}>On this place they also hold: {t.replacements.join(", ")}.</p>
      )}
      {t.can_dismiss &&
        (dismissing ? (
          <form className={cx(FORM_ROW, "print:hidden!")} onSubmit={dismiss}>
            <input
              autoFocus
              className={cx("tracking-normal", NOTE_INPUT)}
              placeholder="Why it will not be done"
              aria-label="Why it will not be done"
              value={reason}
              maxLength={2000}
              required
              onChange={(e) => setReason(e.target.value)}
            />
            <Button type="submit" color="primary" size="sm" isDisabled={!reason.trim()}>
              Dismiss
            </Button>
            <Button color="secondary" size="sm" onClick={() => setDismissing(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className={FORM_ROW}>
            <Button color="secondary" size="sm" onClick={() => setDismissing(true)}>
              Won&apos;t do…
            </Button>
          </div>
        ))}
    </article>
  );
}
