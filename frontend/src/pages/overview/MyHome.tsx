import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import Button from "@/components/Button";
import { BADGE, BADGE_LEVEL, CAPTION, EMPTY_STATE } from "@/components/classes";
import { api, type Me, type ReviewRound } from "@/api/client";
import { PageHeader, SectionCard } from "@/components/PageTemplate";
import { placesText } from "@/hooks/useMe";
import ChangesFeed from "@/pages/changes/ChangesFeed";
import { principalHref } from "@/pages/principals/principalUtils";
import { dueText, roundTitle } from "@/pages/reviews/reviewUtils";
import { LEVEL_SHORT, scopeHref, scopeLabel, treeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

/** A list of rows with a line between them (Home's "needs a look", reviews, places). */
const FEED = "m-0 list-none p-0";
const FEED_ITEM = "flex flex-wrap items-start gap-4 border-t border-t-secondary py-4 first:border-t-0";
const FEED_MAIN = "flex min-w-0 flex-[1_1_240px] flex-col gap-0.5";
const FEED_TITLE = "font-medium [overflow-wrap:anywhere]";
const FEED_META = "text-[0.75rem] text-tertiary [overflow-wrap:anywhere]";
const FEED_ACTIONS = "flex flex-[0_1_auto] flex-wrap gap-1";
/** Two blocks side by side, one under the other when narrow. */
const TWO_COL = "flex flex-wrap items-start gap-6 [&>*]:min-w-0 [&>*]:flex-[1_1_420px] [&>section]:mb-0";
/** One plain-words sentence under the title. */
const SENTENCE = "m-0 text-[1rem] leading-[1.6]";

/**
 * Home for a signed-in person who is not an admin: their places, their
 * reviews, the latest changes there, and their own access.
 */
export default function MyHome({ me }: { me: Me }) {
  const [rounds, setRounds] = useState<ReviewRound[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api.reviews
      .list({ mine: true })
      .then(setRounds)
      .catch(() => setFailed(true));
  }, []);

  const places = me.responsible_for;
  const open = (rounds ?? []).filter((r) => !r.finished_at);

  return (
    <>
      <PageHeader title="Home" sub={`Hello${me.name ? `, ${me.name}` : ""}.`} />

      <p className={SENTENCE}>
        {places.length > 0 ? (
          <>
            You are responsible for <strong>{placesText(places.map((p) => scopeLabel(p.scope_id, p.name)))}</strong>, and
            everything inside {places.length === 1 ? "it" : "them"}. You see who can act there, and your own access.
          </>
        ) : (
          <>You are not responsible for any place, so you see your own access only.</>
        )}
      </p>

      <section className={TWO_COL}>
        <SectionCard
          icon="reviews"
          title="Your reviews"
          tone={open.length ? "warn" : "ok"}
          hint="Each month, decide for each grant on your places: keep, remove, change or ask."
          actions={
            <>
              <Link to="/reviews" className={CAPTION}>
                All reviews →
              </Link>
            </>
          }
        >
          {failed ? (
            <p className="my-[1em] text-error-primary">Could not load your reviews. Reload the page to try again.</p>
          ) : rounds === null ? (
            <p className="my-[1em] text-quaternary">Loading…</p>
          ) : open.length === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>No review waiting for you.</p>
          ) : (
            <ul className={FEED}>
              {open.map((r) => (
                <li key={r.id} className={FEED_ITEM}>
                  <div className={FEED_MAIN}>
                    <span className={FEED_TITLE}>{roundTitle(r)}</span>
                    <span className={FEED_META}>
                      {r.blocking} to decide · {dueText(r.due_at)}
                    </span>
                  </div>
                  <div className={FEED_ACTIONS}>
                    <Button variant="primary" size="sm" to={`/reviews/${r.id}`}>
                      Review
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          icon="places"
          title="Your places"
          hint="You see who can act there, and everything inside them."
        >
          {places.length === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>None. An admin sets who is responsible for each place.</p>
          ) : (
            <ul className={FEED}>
              {places.map((p) => (
                <li key={p.scope_id} className={FEED_ITEM}>
                  <span className={cx(BADGE, BADGE_LEVEL[p.level])}>{LEVEL_SHORT[p.level]}</span>
                  <div className={FEED_MAIN}>
                    <Link to={scopeHref(p.scope_id)} className={FEED_TITLE}>
                      {treeLabel(p.scope_id, p.name, p.level)}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {me.oid && (
            <p className={cx("mb-[1em]", CAPTION)} style={{ marginTop: "var(--space-md)" }}>
              <Link to={principalHref(me.oid)}>Your own access →</Link>
            </p>
          )}
        </SectionCard>
      </section>

      <SectionCard
        icon="changes"
        title="Latest changes in your places"
        hint="Access given or removed there in the last 30 days."
        actions={
          <>
            <Link to="/changes" className={CAPTION}>
              All changes →
            </Link>
          </>
        }
      >
        <ChangesFeed filters={false} paging={false} limit={8} emptyText="No change to access in your places in the last 30 days." />
      </SectionCard>
    </>
  );
}
