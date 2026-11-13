import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { pillClass } from "@/components/chips";
import { BADGE, BADGE_TONE, CAPTION, CARD, EMPTY_STATE } from "@/components/classes";
import Button from "@/components/Button";
import { api, type ReviewRound } from "@/api/client";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { useMe } from "@/hooks/useMe";
import { cx } from "@/components/classes";

import { bar, decided, dueText, periodLabel, roundPlace } from "./reviewUtils";

/** A review's decisions as one bar: kept, to remove, to change, to ask. */
const REVIEW_BAR = "flex h-1.5 overflow-hidden rounded-full bg-tertiary";
const BAR_KEEP = "bg-(--accent-green)";
const BAR_REMOVE = "bg-(--accent-red)";
const BAR_CHANGE = "bg-(--accent-blue)";
const BAR_ASK = "bg-(--accent-orange)";
/** A block that is the page's first <div>: left out on paper. */
const PRINT_SKIP_IF_FIRST = "print:[.page-content>&:first-of-type]:hidden";
/** One review as a card that opens it. */
const ROUND_CARD = cx(
  CARD,
  "flex flex-col gap-1 text-primary no-underline hover:bg-primary_hover focus-visible:[box-shadow:var(--focus-ring)] focus-visible:outline-none",
);

/** Monthly reviews: this month first. Route: /reviews. */
export default function ReviewsPage() {
  const me = useMe();
  const { showToast } = useToast();
  const [rounds, setRounds] = useState<ReviewRound[] | null>(null);
  const [mineOnly, setMineOnly] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const signedIn = !!me?.signed_in;

  const load = useCallback(() => {
    api.reviews
      .list({ mine: signedIn && mineOnly })
      .then((r) => {
        setRounds(r);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [signedIn, mineOnly]);
  useEffect(load, [load]);

  const byPeriod = useMemo(() => {
    const out = new Map<string, ReviewRound[]>();
    for (const r of rounds ?? []) out.set(r.period, [...(out.get(r.period) ?? []), r]);
    return [...out.entries()];
  }, [rounds]);

  const now = Date.now();
  const all = rounds ?? [];
  const current = byPeriod[0]?.[1] ?? [];
  const finishedNow = current.filter((r) => r.finished_at).length;
  const waiting = all.filter((r) => r.can_decide).reduce((n, r) => n + r.blocking, 0);
  const late = all.filter((r) => !r.finished_at && r.due_at && new Date(r.due_at).getTime() < now).length;

  async function openNow() {
    setOpening(true);
    try {
      const s = await api.reviews.open();
      showToast(`${s.rounds_opened} opened · ${s.items_added} grants added`, "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setOpening(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Reviews"
        sub="Every month, the person responsible for a place decides for each grant: keep it, remove it, or ask."
        actions={
          (signedIn || me?.is_admin) && (
            <>
              {signedIn && (
                <button
                  type="button"
                  className={pillClass(mineOnly)}
                  aria-pressed={mineOnly}
                  onClick={() => setMineOnly((v) => !v)}
                >
                  Only mine
                </button>
              )}
              {me?.is_admin && (
                <Button size="sm" type="button" onClick={openNow} disabled={opening}>
                  {opening ? "Opening…" : "Open this month's reviews now"}
                </Button>
              )}
            </>
          )
        }
      />

      {error && <p className="my-[1em] text-error-primary">Could not load the reviews: {error}</p>}
      {!rounds && !error && <p className="my-[1em] text-quaternary">Loading…</p>}
      {rounds && rounds.length === 0 && (
        <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
          <p className={cx("my-[1em]", EMPTY_STATE)}>
            No review yet. Reviews open on their own after the daily check, one per place with someone responsible
            (<Link to="/settings/responsible">Settings › Who is responsible</Link>) and one for the admins.
          </p>
        </div>
      )}

      {rounds && rounds.length > 0 && (
        <StatusTiles>
          <StatusTile
            tone={current.length && finishedNow === current.length ? "ok" : "info"}
            icon="reviews"
            label={byPeriod[0] ? periodLabel(byPeriod[0][0]) : "This month"}
            value={`${finishedNow} of ${current.length} finished`}
            note={current.length === 1 ? "1 review" : `${current.length} reviews, one per place`}
          />
          <StatusTile
            tone={waiting > 0 ? "warn" : "ok"}
            icon="todo"
            label={signedIn ? "Waiting for you" : "Waiting for a decision"}
            value={waiting > 0 ? `${waiting.toLocaleString()} ${waiting === 1 ? "grant" : "grants"}` : "Nothing"}
            note={waiting > 0 ? "Keep, remove, change or ask" : "Every grant you review is decided"}
          />
          <StatusTile
            tone={late > 0 ? "danger" : "ok"}
            icon="calendar"
            label="Past their due date"
            value={late > 0 ? `${late} ${late === 1 ? "review" : "reviews"}` : "None"}
            note={late > 0 ? "Still open after the date set" : "All on time"}
          />
        </StatusTiles>
      )}

      {byPeriod.map(([period, list]) => {
        const done = list.filter((r) => r.finished_at).length;
        return (
          <SectionCard
            key={period}
            icon="reviews"
            tone={done === list.length ? "ok" : "info"}
            title={periodLabel(period)}
            hint={`${list.length} ${list.length === 1 ? "review" : "reviews"}, ${done} finished. Open one to decide its grants.`}
          >
            <div className="grid grid-cols-[repeat(auto-fill,minmax(min(300px,100%),1fr))] gap-4">
              {list.map((r) => (
                <RoundCard key={r.id} round={r} />
              ))}
            </div>
          </SectionCard>
        );
      })}
    </>
  );
}

function RoundCard({ round: r }: { round: ReviewRound }) {
  const b = bar(r);
  const done = decided(r);
  const reviewable = r.progress.total - r.progress.gone;
  return (
    <Link to={`/reviews/${r.id}`} className={ROUND_CARD}>
      <span className="font-semibold">{roundPlace(r)}</span>
      <span className={cx(CAPTION, "text-quaternary")}>
        Reviewer: {r.reviewer_name ?? "—"} ·{" "}
        {r.finished_at ? `finished${r.finished_by_name ? ` by ${r.finished_by_name}` : ""}` : dueText(r.due_at)}
      </span>
      <span className={REVIEW_BAR} aria-hidden="true">
        <span className={BAR_KEEP} style={{ width: `${b.keep}%` }} />
        <span className={BAR_REMOVE} style={{ width: `${b.remove}%` }} />
        <span className={BAR_CHANGE} style={{ width: `${b.change}%` }} />
        <span className={BAR_ASK} style={{ width: `${b.ask}%` }} />
      </span>
      <span className={CAPTION}>
        {done} of {reviewable} decided · {r.progress.keep} kept · {r.progress.remove} to remove
        {r.progress.change > 0 && ` · ${r.progress.change} to change`} · {r.progress.ask} to ask
        {r.progress.gone > 0 && ` · ${r.progress.gone} removed in Azure`}
      </span>
      {r.can_decide && r.blocking > 0 && <span className={cx(BADGE, BADGE_TONE.yours, "self-start")}>{r.blocking} for you</span>}
    </Link>
  );
}
