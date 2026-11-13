import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { decisionClass, linkButtonClass, pillClass } from "@/components/chips";
import {
  BADGE,
  BADGE_TONE,
  CAPTION,
  CARD,
  EMPTY_STATE,
  FILTER_BAR,
  FORM_ROW,
  LEVEL_PILL,
  WHO_ACCENT,
} from "@/components/classes";
import Button from "@/components/Button";
import {
  api,
  type PrincipalMember,
  type ReviewDecisionKind,
  type ReviewItem,
  type ReviewRoundDetail,
} from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import { useToast } from "@/components/Toast";
import { Breadcrumbs, PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { grantHref } from "@/pages/grants/grantUtils";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";

import {
  adviceSummary,
  bar,
  DECISION_WORD,
  decided,
  dueText,
  type ItemFilter,
  matches,
  nextDecision,
  removeCommands,
  roundPlace,
  roundTitle,
} from "./reviewUtils";

const FILTERS: { key: ItemFilter; label: string }[] = [
  { key: "open", label: "To decide" },
  { key: "ask", label: "To ask" },
  { key: "remove", label: "To remove" },
  { key: "change", label: "To change" },
  { key: "keep", label: "Kept" },
  { key: "gone", label: "Removed in Azure" },
  { key: "all", label: "All" },
];

/** A review's decisions as one bar: kept, to remove, to change, to ask. */
const REVIEW_BAR = "flex h-1.5 overflow-hidden rounded-full bg-tertiary";
const BAR_KEEP = "bg-(--accent-green)";
const BAR_REMOVE = "bg-(--accent-red)";
const BAR_CHANGE = "bg-(--accent-blue)";
const BAR_ASK = "bg-(--accent-orange)";
/** A block that is the page's first <div>: left out on paper. */
const PRINT_SKIP_IF_FIRST = "print:[.page-content>&:first-of-type]:hidden";
const MUTED_CAPTION = cx(CAPTION, "text-quaternary");
const HELP_P = "mx-0 mt-0 mb-1 leading-[1.5]";
/**
 * The note typed for "ask" or "change". The app's input look stays (the
 * element style wins on size, border and colour); it takes the row's width.
 */
const NOTE_INPUT = "flex-[1_1_240px] [font-style:inherit] [font-weight:inherit] [line-height:inherit]";

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** One month's review of one place: Keep / Remove / Ask per grant. Route: /reviews/:id. */
export default function ReviewPage() {
  const { id = "" } = useParams();
  const rid = Number(id);
  const { showToast } = useToast();
  const [data, setData] = useState<ReviewRoundDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ItemFilter>("open");
  const [busy, setBusy] = useState<number | null>(null);
  const [confirmAccept, setConfirmAccept] = useState(false);

  const load = useCallback(() => {
    api.reviews
      .detail(rid)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [rid]);
  useEffect(load, [load]);

  const counts = useMemo(() => {
    const c: Record<ItemFilter, number> = { open: 0, ask: 0, remove: 0, change: 0, keep: 0, gone: 0, all: 0 };
    for (const i of data?.items ?? []) for (const f of FILTERS) if (matches(i, f.key)) c[f.key] += 1;
    return c;
  }, [data]);

  async function decide(item: ReviewItem, decision: ReviewDecisionKind | null, note?: string | null) {
    setBusy(item.id);
    try {
      await api.reviews.decide(rid, item.id, decision, note);
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function acceptAll() {
    setConfirmAccept(false);
    try {
      const c = await api.reviews.acceptAdvice(rid);
      showToast(`Advice accepted: ${c.keep} keep · ${c.remove} remove · ${c.change} change · ${c.ask} ask`, "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function finish() {
    try {
      await api.reviews.finish(rid);
      showToast("Review finished", "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  function copyCommands() {
    if (!data) return;
    navigator.clipboard
      ?.writeText(removeCommands(data.items))
      .then(() => showToast("Commands copied. Review them before running.", "success"))
      .catch(() => showToast("Could not copy", "error"));
  }

  if (error && !data) {
    return (
      <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
        <p className="my-[1em] text-error-primary">{error.startsWith("404") ? "This review does not exist." : `Could not load: ${error}`}</p>
        <Link to="/reviews">← Reviews</Link>
      </div>
    );
  }
  if (!data) return <div className={cx(CARD, "text-quaternary", PRINT_SKIP_IF_FIRST)}>Loading review…</div>;

  const b = bar(data);
  const reviewable = data.progress.total - data.progress.gone;
  const shown = data.items.filter((i) => matches(i, filter));
  const toRemove = data.items.filter((i) => i.decision === "remove" && !i.gone_at).length;
  const toChange = data.items.filter((i) => i.decision === "change" && !i.gone_at).length;
  const advice = adviceSummary(data.items);

  return (
    <>
      <Breadcrumbs items={[{ label: "Reviews", to: "/reviews" }, { label: roundTitle(data) }]} />
      <PageHeader
        title={roundTitle(data)}
        sub={
          <>
            Opened {formatDay(data.opened_at)}, due {formatDay(data.due_at)} ({data.finished_at ? "finished" : dueText(data.due_at)}) ·{" "}
            {data.scope_id ? (
              <Link to={scopeHref(data.scope_id)}>{roundPlace(data)}</Link>
            ) : (
              roundPlace(data)
            )}{" "}
            and everything inside it that nobody else reviews · reviewer: {data.reviewer_name ?? "—"}
          </>
        }
        actions={
          data.finished_at ? (
            <span className={cx(BADGE, BADGE_TONE.done)}>Finished{data.finished_by_name ? ` by ${data.finished_by_name}` : ""}</span>
          ) : (
            data.can_decide && (
              <Button
                variant="primary"
                size="sm"
                type="button"
                onClick={finish}
                disabled={data.blocking > 0}
                title={data.blocking > 0 ? `${data.blocking} still to decide or asked` : undefined}
              >
                Finish review
              </Button>
            )
          )
        }
      />

      <StatusTiles>
        <StatusTile
          tone={decided(data) === reviewable ? "ok" : "info"}
          icon="reviews"
          label="Decided"
          value={`${decided(data)} of ${reviewable}`}
          note={`${data.progress.keep} kept · ${data.progress.remove} to remove · ${data.progress.ask} asked`}
        />
        <StatusTile
          tone={data.blocking > 0 ? "warn" : "ok"}
          icon="todo"
          label="Still open"
          value={data.blocking > 0 ? `${data.blocking} ${data.blocking === 1 ? "grant" : "grants"}` : "None"}
          note={data.blocking > 0 ? "To decide, or asked and not answered" : "Ready to finish"}
        />
        <StatusTile
          tone={toRemove + toChange > 0 ? "warn" : "ok"}
          icon="shield"
          label="To make in Azure"
          value={`${toRemove} to remove · ${toChange} to change`}
          note="Each becomes a to-do when the review is finished"
        />
      </StatusTiles>

      <SectionCard
        icon="reviews"
        title="Progress"
        hint="Kept, to remove, to change and asked, out of every grant in this review."
      >
        <span className={cx(REVIEW_BAR, "mt-2 h-2.5")} aria-hidden="true">
          <span className={BAR_KEEP} style={{ width: `${b.keep}%` }} />
          <span className={BAR_REMOVE} style={{ width: `${b.remove}%` }} />
          <span className={BAR_CHANGE} style={{ width: `${b.change}%` }} />
          <span className={BAR_ASK} style={{ width: `${b.ask}%` }} />
        </span>
        {data.can_decide && advice.total > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {confirmAccept ? (
              <>
                <span>
                  Accept the advice on {advice.total} {advice.total === 1 ? "grant" : "grants"}: {advice.keep} keep ·{" "}
                  {advice.remove} remove · {advice.change} change · {advice.ask} ask?
                </span>
                <Button variant="primary" size="sm" type="button" onClick={acceptAll}>
                  Accept
                </Button>
                <Button size="sm" type="button" onClick={() => setConfirmAccept(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <>
                <span className={MUTED_CAPTION}>
                  Each grant shows the playbook&apos;s advice. Check the ones that matter, then accept the rest at once.
                </span>
                <Button size="sm" type="button" onClick={() => setConfirmAccept(true)}>
                  Accept all advice ({advice.total})
                </Button>
              </>
            )}
          </div>
        )}
        {!data.can_decide && !data.finished_at && (
          <p className={cx("my-[1em]", MUTED_CAPTION)}>Only the reviewer or an admin can decide here.</p>
        )}
      </SectionCard>

      <div className={FILTER_BAR}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={pillClass(filter === f.key)}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label} <span className="text-tertiary">{counts[f.key]}</span>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-[minmax(0,1.8fr)_minmax(260px,1fr)] items-start gap-6 max-[960px]:grid-cols-[minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          {shown.length === 0 ? (
            <p className={cx("my-[1em]", CARD, EMPTY_STATE, "px-0")}>{filter === "open" ? "Nothing left to decide here." : "Nothing in this list."}</p>
          ) : (
            shown.map((i) => (
              <ItemCard key={i.id} item={i} canDecide={data.can_decide} busy={busy === i.id} onDecide={decide} />
            ))
          )}
        </div>
        <aside className="flex flex-col gap-4">
          <SectionCard
            icon="todo"
            title="To remove or change"
            hint="RBAC Lens never changes Azure. Finishing the review turns each remove and change into a to-do, with a ready-to-run command; the next daily check ticks it off."
            actions={
              <>
                <span className={MUTED_CAPTION}>
                  {toRemove} · {toChange}
                </span>
              </>
            }
          >
            {data.finished_at && (toRemove > 0 || toChange > 0) && (
              <p className="my-[1em]">
                <Link to={`/todos?round=${data.id}`}>See the to-dos of this review →</Link>
              </p>
            )}
            <div className={FORM_ROW}>
              <Button size="sm" type="button" onClick={copyCommands} disabled={toRemove === 0}>
                Copy commands
              </Button>
              <DownloadLink href={api.reviews.exportUrl(rid)}>
                Export list
              </DownloadLink>
            </div>
          </SectionCard>
          <SectionCard
            icon="history"
            title="How to read this"
          >
            <div className={MUTED_CAPTION}>
              <p className={HELP_P}>A grant is reviewed on the place where it was given. Access inherited from above is reviewed there.</p>
              <p className={HELP_P}>Azure never records reads. Someone who only looks, or only reads data, shows no change, so ask before removing.</p>
              <p className={HELP_P}>A grant kept last month and unchanged since starts as kept.</p>
              <p className={HELP_P}>Nobody reviews their own access: it comes to the next person responsible above, or to the admins.</p>
              <p className={HELP_P}>
                <strong>Ask</strong> keeps the item open, with a reminder in 7 days. When email is connected, the person
                is asked by email (Settings › Notifications).
              </p>
            </div>
          </SectionCard>
        </aside>
      </div>
    </>
  );
}

function ItemCard({
  item: i,
  canDecide,
  busy,
  onDecide,
}: {
  item: ReviewItem;
  canDecide: boolean;
  busy: boolean;
  onDecide: (item: ReviewItem, decision: ReviewDecisionKind | null, note?: string | null) => void;
}) {
  const [asking, setAsking] = useState<"ask" | "change" | null>(null);
  const [note, setNote] = useState(i.note ?? "");
  const [members, setMembers] = useState<PrincipalMember[] | null>(null);
  const [membersRead, setMembersRead] = useState(false);
  const isGroup = i.principal_type === "Group";
  const gone = i.gone_at !== null;

  function choose(d: ReviewDecisionKind) {
    const next = nextDecision(i.decision, d);
    if (next === "ask" || next === "change") {
      if (next === "change" && !note.trim() && i.suggested === "change") setNote(i.suggested_title ?? "");
      setAsking(next);
    } else onDecide(i, next);
  }
  const advised = i.decision === null && !gone && i.suggested;

  function toggleMembers() {
    if (members !== null) return setMembers(null);
    api.principals
      .detail(i.principal_id)
      .then((d) => {
        setMembersRead(d.members_read ?? false);
        setMembers(d.members ?? []);
      })
      .catch(() => setMembers([]));
  }

  return (
    <article className={cx(CARD, "flex flex-col gap-1", gone && "opacity-70")}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to={principalHref(i.principal_id)} className="font-semibold [overflow-wrap:anywhere]">
          {i.principal_name ?? i.principal_id}
        </Link>
        <span className={MUTED_CAPTION}>{typeLabel(i.principal_type)}</span>
        <span className={cx(LEVEL_PILL, WHO_ACCENT[i.level])}>{LEVEL_INFO[i.level].title}</span>
        {i.moved_from_scope && <span className={cx(BADGE, BADGE_TONE.yours)}>their own place</span>}
        {i.carried && <span className={cx(BADGE, BADGE_TONE.done)}>kept last month</span>}
        {gone && <span className={cx(BADGE, BADGE_TONE.done)}>removed in Azure</span>}
      </div>
      <p className="m-0 [overflow-wrap:anywhere]">
        {i.role_name ?? "A role"} on <Link to={scopeHref(i.scope)}>{scopeLabel(i.scope, i.scope_name)}</Link> ·{" "}
        <Link to={grantHref(i.grant_id)}>details</Link>
      </p>
      {i.moved_from_scope && (
        <p className={cx("my-[1em]", MUTED_CAPTION)}>
          The person responsible for {i.moved_from_name ?? i.moved_from_scope} holds this access, so it comes to you
          rather than to them.
        </p>
      )}
      {isGroup && (
        <button type="button" className={linkButtonClass(true)} onClick={toggleMembers}>
          {members === null ? "Show who is in the group" : "Hide members"}
        </button>
      )}
      {members && (
        <p className={cx("my-[1em]", CAPTION)}>
          {members.length === 0
            ? membersRead
              ? "No members in Entra ID: nobody gets this access today."
              : "The members could not be read from Microsoft Graph."
            : members.slice(0, 15).map((m) => m.member_name ?? m.member_id).join(", ") +
              (members.length > 15 ? ` and ${members.length - 15} more` : "")}
          <span className="text-quaternary"> · membership is managed in Entra ID</span>
        </p>
      )}
      {advised && (
        <p className={cx("my-1 border-l-[3px] pl-2", i.suggested === "keep" ? "border-l-(--accent-green)" : "border-l-(--accent-orange)")}>
          <span className={MUTED_CAPTION}>Advice:</span> {i.suggested_title}{" "}
          <span className={MUTED_CAPTION}>
            · suggests <strong>{DECISION_WORD[i.suggested!]}</strong>
            {i.suggested_rule && ` · ${i.suggested_rule.split(" ")[0]}`}
          </span>{" "}
          <Link to={grantHref(i.grant_id)} className={CAPTION}>
            why
          </Link>
        </p>
      )}
      {i.note && !asking && <p className={cx("my-[1em]", CAPTION)}>Note: {i.note}</p>}
      {i.decision && (
        <p className={cx("my-[1em]", MUTED_CAPTION)}>
          {i.decision === "keep"
            ? "Kept"
            : i.decision === "remove"
              ? "To remove"
              : i.decision === "change"
                ? "To change"
                : "To ask"}
          {i.decided_by_name && ` · ${i.decided_by_name}`}
          {i.ask_remind_at && ` · reminder ${formatDay(i.ask_remind_at)}`}
        </p>
      )}
      {i.decision === "remove" && <code className="block rounded-[4px] bg-(--bg-input) px-2 py-1 text-[0.75rem] [overflow-wrap:anywhere]">{i.remove_command}</code>}

      {canDecide && !gone && (
        <div className={cx(FORM_ROW, "mt-1")}>
          {(["keep", "remove", "change", "ask"] as ReviewDecisionKind[]).map((d) => (
            <button
              key={d}
              type="button"
              className={decisionClass(d, i.decision === d, !!advised && i.suggested === d)}
              aria-pressed={i.decision === d}
              title={advised && i.suggested === d ? "Suggested by the advice" : undefined}
              disabled={busy}
              onClick={() => choose(d)}
            >
              {DECISION_WORD[d]}
            </button>
          ))}
        </div>
      )}
      {asking && (
        <form
          className={cx(FORM_ROW, "print:hidden!")}
          onSubmit={(e) => {
            e.preventDefault();
            const kind = asking;
            setAsking(null);
            onDecide(i, kind, note.trim() || null);
          }}
        >
          <input
            autoFocus
            className={cx("tracking-normal", NOTE_INPUT)}
            placeholder={asking === "change" ? "What to change: PIM, a smaller role, another place…" : "What to ask (optional)"}
            aria-label={asking === "change" ? "What to change" : "What to ask"}
            value={note}
            maxLength={2000}
            required={asking === "change"}
            onChange={(e) => setNote(e.target.value)}
          />
          <Button variant="primary" size="sm" type="submit" disabled={asking === "change" && !note.trim()}>
            {asking === "change" ? "Mark to change" : "Mark as a question"}
          </Button>
          <Button size="sm" type="button" onClick={() => setAsking(null)}>
            Cancel
          </Button>
        </form>
      )}
    </article>
  );
}
