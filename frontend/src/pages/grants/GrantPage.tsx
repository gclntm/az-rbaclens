import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  api,
  type CapabilityLevel,
  type CurrentFinding,
  type DecisionWrite,
  type GrantDetail,
  type ReviewDecisionKind,
} from "@/api/client";
import AdviceBox from "@/components/advice/AdviceBox";
import { decisionClass } from "@/components/chips";
import {
  CAPTION,
  CARD,
  EMPTY_STATE,
  FORM_ROW,
  LEVEL_PILL,
  TAG,
  TAG_TONE,
  WHO_ACCENT,
} from "@/components/classes";
import FindingCard from "@/components/FindingCard";
import { RoleLink } from "@/components/RoleDetailDrawer";
import { Breadcrumbs, PageHeader, SectionCard, StatusTile, StatusTiles, type Tone } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { formatDateTime, LEVEL_LABEL, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { nextDecision, periodLabel } from "@/pages/reviews/reviewUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";

import { agoText, formatDay, givenByLabel, lastChangeText } from "./grantUtils";

/** A block that is the page's first <div>: left out on paper (it says "loading" or "not found"). */
const PRINT_SKIP_IF_FIRST = "print:[.page-content>&:first-of-type]:hidden";

/** The grant's facts: names on the left, answers on the right; one column on a phone. */
const FACTS =
  "m-0 grid grid-cols-[minmax(96px,140px)_minmax(0,1fr)] gap-x-6 gap-y-4 max-[640px]:grid-cols-[minmax(0,1fr)] max-[640px]:gap-1";
const FACT_DT = "font-semibold text-tertiary";
const FACT_DD = "m-0 [overflow-wrap:anywhere] max-[640px]:mb-2";
/** A list or a note under an answer: on its own line. The first list in an answer sits flush. */
const FACT_LIST = "flex-col gap-0.5 list-none p-0";
const FACT_NOTE = cx(CAPTION, "mt-1 block text-quaternary");
/** A list of FindingCards (each renders its <li>), as on the findings page. */
const FINDINGS_LIST =
  "m-0 list-none p-0 " +
  "[&>li]:flex [&>li]:flex-wrap [&>li]:items-start [&>li]:gap-4 [&>li]:border-t [&>li]:border-t-secondary [&>li]:px-6 [&>li]:py-4 [&>li:first-child]:border-t-0 " +
  "[&_.sev-dot]:mt-[7px]";

/** "a, b, c, d and 12 more" for a long comma-separated list. */
function shortList(text: string, keep = 4): string {
  const parts = text.split(", ");
  return parts.length > keep ? `${parts.slice(0, keep).join(", ")} and ${parts.length - keep} more` : text;
}

/**
 * One grant: who, can do what, where, since when, how, and is it used.
 * Route: /grants/:grantId (the grant fingerprint, stable across daily checks).
 * Keep / Remove / Ask come from this month's review item, when a round covers the grant.
 */
const LEVEL_TONE: Record<CapabilityLevel, Tone> = {
  access: "danger",
  keys: "danger",
  change: "warn",
  data: "warn",
  view: "ok",
  unknown: "warn",
};

export default function GrantPage() {
  const { grantId = "" } = useParams();
  const { showToast } = useToast();
  const [grant, setGrant] = useState<GrantDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api.grants
      .detail(grantId)
      .then((g) => {
        setGrant(g);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [grantId]);

  useEffect(() => {
    setGrant(null);
    load();
  }, [load]);

  async function review(decision: ReviewDecisionKind) {
    const ref = grant?.review;
    if (!ref) return;
    const next = nextDecision(ref.decision, decision);
    try {
      await api.reviews.decide(ref.round_id, ref.item_id, next);
      showToast(
        next === null ? "Decision cleared" : next === "keep" ? "Kept" : next === "remove" ? "Marked to remove" : "Marked as a question",
        "success",
      );
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function run(f: CurrentFinding, action: () => Promise<unknown>, label: string) {
    setBusy(f.fingerprint);
    try {
      await action();
      showToast(label, "success");
      load();
    } catch (e) {
      showToast(`Could not save: ${(e as Error).message}`, "error");
    } finally {
      setBusy(null);
    }
  }

  if (error && !grant) {
    return (
      <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
        <p className="my-[1em] text-error-primary">
          {error.startsWith("404")
            ? "This grant is not in the latest daily check. It may have been removed in Azure."
            : `Could not load this grant: ${error}`}
        </p>
        <Link to="/places">← Places</Link>
      </div>
    );
  }
  if (!grant) {
    return (
      <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
        <p className="my-[1em] text-quaternary">Loading grant…</p>
      </div>
    );
  }

  const who = grant.principal_name ?? grant.principal_id;
  const where = scopeLabel(grant.scope, grant.scope_name);
  const role = grant.role_name ?? "an unnamed role";
  const by = givenByLabel(grant);
  const open = grant.findings.filter((f) => f.status === "open");

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Places", to: "/places" },
          ...grant.ancestors.map((a) => ({ label: scopeLabel(a.id, a.name), to: scopeHref(a.id) })),
          { label: where, to: scopeHref(grant.scope) },
          { label: `${who} · ${role}` },
        ]}
      />

      <PageHeader
        title={
          <>
            {who} · {role} on {where}
          </>
        }
        sub={
          <>
            <span className={cx(LEVEL_PILL, WHO_ACCENT[grant.level])}>{LEVEL_INFO[grant.level].title}</span>
            {grant.access_as_of && <> · access as of {formatDateTime(grant.access_as_of)}</>}
          </>
        }
      />

      <StatusTiles>
        <StatusTile
          tone={LEVEL_TONE[grant.level]}
          icon="shield"
          label="What it allows"
          value={LEVEL_INFO[grant.level].title}
          note={grant.inside_count > 0 ? `Here and in ${grant.inside_count} ${grant.inside_count === 1 ? "place" : "places"} inside` : "Here"}
        />
        <StatusTile
          tone="info"
          icon="changes"
          label="Used?"
          value={grant.last_activity_at ? agoText(grant.last_activity_at) : "No change recorded"}
          note="Azure records changes, never reads"
        />
        <StatusTile
          tone={open.length > 0 ? "warn" : "ok"}
          icon="look"
          label="Needs a look"
          value={open.length > 0 ? `${open.length} open` : "Nothing open"}
          note={grant.findings.length > open.length ? `${grant.findings.length - open.length} decided` : "Reasons on this grant"}
        />
      </StatusTiles>

      {grant.advice && <AdviceBox advice={grant.advice} grantId={grantId} />}

      <SectionCard icon="shield" title="This grant" hint="Who, what they can do, where, since when and how.">
        <dl className={FACTS}>
          <dt className={FACT_DT}>Who</dt>
          <dd className={FACT_DD}>
            <Link to={principalHref(grant.principal_id)}>{who}</Link>{" "}
            <span className="text-quaternary">· {typeLabel(grant.principal_type)}</span>
            {grant.kind === "group" && grant.member_count !== null && (
              <span className="text-quaternary">
                {" "}
                · {grant.member_count} {grant.member_count === 1 ? "member" : "members"}, each with this access
              </span>
            )}
            {!grant.principal_name && <span className={cx(TAG, TAG_TONE.unresolved)}>no longer in Entra ID?</span>}
          </dd>

          <dt className={FACT_DT}>Can do</dt>
          <dd className={FACT_DD}>
            <ul className={cx(FACT_LIST, "m-0 block")}>
              {grant.powers.map((p) => (
                <li key={p.key} className={p.allowed ? undefined : "text-tertiary"}>
                  <span aria-hidden="true" className={cx("inline-block w-[1.25em] font-bold", p.allowed && "text-success-primary")}>
                    {p.allowed ? "✓" : "–"}
                  </span>
                  <span className="absolute -m-px size-px overflow-hidden [clip:rect(0,0,0,0)] [border:0] p-0 whitespace-nowrap">{p.allowed ? "Yes:" : "No:"}</span>
                  {p.label}
                  {p.allowed && p.detail && <span className="text-quaternary"> · {shortList(p.detail)}</span>}
                </li>
              ))}
            </ul>
            <span className={FACT_NOTE}>
              Role: <RoleLink roleId={grant.role_definition_id} roleName={grant.role_name} />
              {(grant.role_type ?? "").endsWith("CustomRole") && " (custom role)"}
            </span>
          </dd>

          <dt className={FACT_DT}>Where</dt>
          <dd className={FACT_DD}>
            <Link to={scopeHref(grant.scope)}>{where}</Link>{" "}
            <span className="text-quaternary">({LEVEL_LABEL[grant.scope_level].toLowerCase()})</span>
            {grant.inside_count > 0 && (
              <span className="text-quaternary">
                {" "}
                and everything inside it ({grant.inside_count} {grant.inside_count === 1 ? "place" : "places"} known)
              </span>
            )}
          </dd>

          <dt className={FACT_DT}>Since when</dt>
          <dd className={FACT_DD}>
            {grant.since ? (
              <>
                {formatDay(grant.since)} <span className="text-quaternary">({agoText(grant.since)})</span>
                {by && <>, given by {by}</>}
              </>
            ) : (
              <span className="text-quaternary">Azure did not record when this grant was made.</span>
            )}
          </dd>

          <dt className={FACT_DT}>How</dt>
          <dd className={FACT_DD}>
            {grant.kind === "group" ? "Through the group: everyone in it has this access." : "Given directly."}
            {grant.activated && " Switched on through PIM"}
            {grant.until ? ` Ends ${formatDay(grant.until)}.` : grant.activated ? "." : " No end date."}
            {grant.condition_kind && (
              <div className={cx("mt-1 block text-[0.8125rem] text-success-primary [overflow-wrap:anywhere]", grant.condition_kind === "other" && "text-warning-primary")}>
                Limited by a condition: {grant.condition_text ?? "one RBAC Lens cannot read"}
              </div>
            )}
          </dd>

          <dt className={FACT_DT}>Used?</dt>
          <dd className={FACT_DD}>
            {lastChangeText(grant.last_activity_at)}
            {grant.last_activity_at && <span className="text-quaternary"> (anywhere in Azure)</span>}.
            {grant.top_operations.length > 0 && (
              <ul className={cx(FACT_LIST, "mx-0 mt-1 mb-0 block")}>
                {grant.top_operations.map((o) => (
                  <li key={o.operation} title={o.operation}>
                    {o.display} <span className="text-quaternary">· {o.count}×</span>
                  </li>
                ))}
              </ul>
            )}
            <span className={FACT_NOTE}>
              Azure records changes, never reads. Someone who only looks or reads data always shows no change.
            </span>
          </dd>
        </dl>
      </SectionCard>

      {grant.review && (
        <SectionCard
          icon="reviews"
          title="Is this right?"
          tone={grant.review.decision ? "ok" : "warn"}
          hint="This grant is in a review: keep it, remove it, or ask."
          actions={
            <>
              <Link to={`/reviews/${grant.review.round_id}`} className={CAPTION}>
                {periodLabel(grant.review.period)} review · {grant.review.reviewer_name ?? "—"} →
              </Link>
            </>
          }
        >
          {grant.review.can_decide ? (
            <div className={cx(FORM_ROW, "mt-1")}>
              {(["keep", "remove", "ask"] as ReviewDecisionKind[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  className={decisionClass(d, grant.review?.decision === d)}
                  aria-pressed={grant.review?.decision === d}
                  onClick={() => review(d)}
                >
                  {d === "keep" ? "Keep" : d === "remove" ? "Remove" : "Not sure, ask"}
                </button>
              ))}
            </div>
          ) : (
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
              {grant.review.decision
                ? `Decided: ${grant.review.decision}.`
                : "Not decided yet."}{" "}
              Only the reviewer or an admin can decide.
            </p>
          )}
          {grant.review.decision === "change" && (
            <p className={cx("my-[1em]", CAPTION)}>
              Marked to change (PIM, a smaller role, another place). The note and the to-do are on the review page.
            </p>
          )}
          {grant.review.decision === "remove" && (
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
              RBAC Lens never changes Azure. The command to run is on the review page; the next daily check shows the
              grant gone.
            </p>
          )}
          {grant.review.decision === "ask" && (
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>Marked as a question. It stays open, with a reminder in 7 days.</p>
          )}
        </SectionCard>
      )}

      <SectionCard
        icon="look"
        title="Needs a look"
        tone={open.length ? "warn" : "ok"}
        hint="What the checks found on this grant, each reason on its own."
        actions={
          <>
            <span className={cx(CAPTION, "text-quaternary")}>
              {open.length === 0 ? "Nothing open" : `${open.length} open`}
            </span>
          </>
        }
      >
        {grant.findings.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>Nothing to look at on this grant.</p>
        ) : (
          <ul className={FINDINGS_LIST}>
            {grant.findings.map((f) => (
              <FindingCard
                key={f.fingerprint}
                finding={f}
                busy={busy === f.fingerprint}
                onDecide={(body: DecisionWrite, label) => run(f, () => api.findings.decide(f.fingerprint, body), label)}
                onReopen={() => run(f, () => api.findings.reopen(f.fingerprint), "Reopened")}
              />
            ))}
          </ul>
        )}
      </SectionCard>
    </>
  );
}
