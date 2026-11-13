import type { AdviceWeight, Assessment, ControlStatus, Report, ReportGrant, ReportRecommendation } from "@/api/client";
import { controlsText, ruleId, WEIGHT_LABEL } from "@/components/advice/adviceUtils";
import { periodLabel } from "@/pages/reviews/reviewUtils";
import { formatDateTime, scopeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import {
  actionRecs,
  controlFigure,
  deltaText,
  deltaTrend,
  groupGrants,
  placesText,
  privilegedSummary,
  rowsToPrint,
  shownText,
} from "./reportPrintUtils";
import { cssString, hoursText, STATUS_LABEL, statusTrend } from "./reportUtils";

const KIND: Record<ReportGrant["kind"], string> = { person: "person", group: "group", app: "app", other: "other" };
const TREND = { better: "better", worse: "worse", same: "same" } as const;
const TOP_ACTIONS = 6;

// Every look below is for paper only: on screen the whole article is hidden.
const MUTED = "print:text-[8.5pt] print:text-[#64748b]";
const H2 = "print:mx-0 print:mt-[14pt] print:mb-[6pt] print:text-[12pt] print:font-semibold print:break-after-avoid";
const H3 = "print:mx-0 print:mt-[10pt] print:mb-[4pt] print:text-[10pt] print:font-semibold print:break-after-avoid";
const NUM = "print:text-right print:tabular-nums";
const EXACT = "print:[print-color-adjust:exact] print:[-webkit-print-color-adjust:exact]";
const TREND_TEXT = { better: "print:text-[#047857]", worse: "print:text-[#b91c1c]", same: "" } as const;

const BIG = "print:text-[20pt] print:leading-[1.15] print:font-semibold print:tabular-nums";
const BIG_TONE = { must: `${BIG} print:text-[#b91c1c]`, should: `${BIG} print:text-[#b45309]` } as const;
const BOX = "print:rounded-[4pt] print:border-[0.75pt] print:break-inside-avoid";

const PILL = `print:inline-block print:rounded-[8pt] print:px-[5pt] print:py-0 print:text-[7.5pt] print:font-semibold print:whitespace-nowrap ${EXACT}`;
const PILL_RED = `${PILL} print:bg-[#fee2e2] print:text-[#b91c1c]`;
const PILL_AMBER = `${PILL} print:bg-[#fef3c7] print:text-[#b45309]`;
const STATUS_PILL: Record<ControlStatus, string> = {
  not_met: PILL_RED,
  partly: PILL_AMBER,
  met: `${PILL} print:bg-[#d1fae5] print:text-[#047857]`,
};
const WEIGHT_PILL: Record<AdviceWeight, string> = { must: PILL_RED, should: PILL_AMBER, could: PILL, info: PILL };

const TABLE = "print:mx-0 print:my-[3pt] print:w-full print:table-fixed print:border-collapse";
const THEAD = "print:table-header-group";
const TR = "print:break-inside-avoid";
const TH =
  "print:border-b-[0.75pt] print:border-b-[#cbd5e1] print:px-[4pt] print:py-[2pt] print:text-left print:text-[8pt] print:font-semibold print:text-[#64748b]";
const TD =
  "print:border-b-[0.5pt] print:border-b-[#e2e8f0] print:px-[4pt] print:py-[2.5pt] print:align-top print:[overflow-wrap:anywhere]";
const TD_FIRST = cx(TD, "print:py-[3pt]");
const TD_DETAIL = cx(TD, "print:text-[8.5pt]");

const REC = `print:mx-0 print:mt-0 print:mb-[10pt] print:border-l-[2pt] print:pl-[7pt] ${EXACT}`;
const REC_TONE: Record<AdviceWeight, string> = {
  must: `${REC} print:border-l-[#dc2626]`,
  should: `${REC} print:border-l-[#d97706]`,
  could: `${REC} print:border-l-[#cbd5e1]`,
  info: `${REC} print:border-l-[#cbd5e1]`,
};

const FACTS =
  "print:m-0 print:grid print:grid-cols-[max-content_minmax(0,1fr)] print:[align-content:start] print:gap-x-[8pt] print:gap-y-[2pt]";
const DT = "print:text-[#64748b]";
const DT_HEAD = "print:col-span-full print:mb-[2pt] print:text-[10pt] print:font-semibold print:text-[#0f172a]";
const DD = "print:m-0";

const place = (g: ReportGrant) => scopeLabel(g.scope, g.scope_name);

/**
 * The report as printed (print or save as PDF): a one-page summary, the actions once, then
 * the evidence. Hidden on screen; the screen keeps the full sections (ReportSections).
 * ``baseline`` is the saved assessment the numbers are compared with, if any.
 */
export default function ReportPrint({ report: r, baseline }: { report: Report; baseline: Assessment | null }) {
  const recs = actionRecs(r);
  const before = baseline?.numbers ?? {};
  return (
    <article className="hidden print:block print:[font-family:Inter,system-ui,sans-serif] print:text-[9.5pt] print:leading-[1.4] print:text-[#0f172a]">
      <header>
        <p className="my-[1em] print:m-0 print:text-[7.5pt] print:tracking-[0.06em] print:text-[#64748b] print:uppercase">Access assessment · ISO/IEC 27001:2022 Annex A</p>
        <h1 className="print:mx-0 print:my-[2pt] print:text-[20pt] print:font-bold print:[overflow-wrap:anywhere]">{r.scope_id ? scopeLabel(r.scope_id, r.scope_name) : r.scope_name}</h1>
        <p className="my-[1em] print:mx-0 print:mt-0 print:mb-[12pt] print:text-[#475569]">
          Access as of {r.access_as_of ? formatDateTime(r.access_as_of) : "—"} · {r.identities} identities ·{" "}
          {r.grants} grants
          {baseline && ` · compared with the assessment of ${formatDateTime(baseline.created_at)}`}
        </p>
      </header>

      <div className="print:grid print:grid-cols-4 print:gap-[6pt]">
        <Tile label="Must do" value={r.numbers.must} before={before.must} tone="must" note="grants" />
        <Tile label="Should do" value={r.numbers.should} before={before.should} tone="should" note="grants" />
        <Tile label="Can change access" value={r.numbers.standing_admin_high} before={before.standing_admin_high} note="standing, high up" />
        <Tile label="Can read keys" value={r.numbers.keys_high} before={before.keys_high} note="standing, high up" />
      </div>

      <h2 className={H2}>Controls</h2>
      <div className="print:grid print:grid-cols-3 print:gap-[5pt]">
        {r.controls.map((c) => {
          const trend = baseline ? statusTrend(c.status, baseline.statuses[c.control]) : null;
          return (
            <div key={c.control} className={cx(BOX, "print:border-[#e2e8f0] print:px-[7pt] print:py-[5pt]")}>
              <div>
                <span className={STATUS_PILL[c.status]}>{STATUS_LABEL[c.status]}</span>{" "}
                <strong>A.{c.control}</strong> {c.name}
              </div>
              <div className={MUTED}>
                {controlFigure(c.control, r)}
                {trend && trend !== "same" && <span className={TREND_TEXT[trend]}> · {TREND[trend]}</span>}
              </div>
            </div>
          );
        })}
      </div>

      <h2 className={H2}>What to do first</h2>
      {recs.length === 0 ? (
        <p className="my-[1em]">No must-do or should-do advice: nothing stands out.</p>
      ) : (
        <table className={TABLE}>
          <colgroup>
            <col style={{ width: "13%" }} />
            <col />
            <col style={{ width: "9%" }} />
            <col style={{ width: "22%" }} />
          </colgroup>
          <tbody>
            {recs.slice(0, TOP_ACTIONS).map((rec) => (
              <tr key={rec.rule} className={TR}>
                <td className={TD_FIRST}>
                  <span className={WEIGHT_PILL[rec.weight]}>{WEIGHT_LABEL[rec.weight]}</span>
                </td>
                <td className={TD_FIRST}>{rec.title}</td>
                <td className={cx(TD_FIRST, NUM)}>{rec.count}</td>
                <td className={cx(TD_FIRST, MUTED)}>{rec.controls.map((c) => `A.${c}`).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {recs.length > TOP_ACTIONS && <p className={cx("my-[1em]", MUTED)}>{recs.length - TOP_ACTIONS} more in Actions.</p>}

      {recs.length > 0 && (
        <section className="print:mt-0 print:break-before-page">
          <h2 className={cx(H2, "print:mt-0")}>Actions</h2>
          {recs.map((rec) => (
            <Action key={rec.rule} rec={rec} />
          ))}
        </section>
      )}

      <section className="print:mt-0 print:break-before-page">
        <h2 className={cx(H2, "print:mt-0")}>Evidence</h2>
        <Privileged report={r} />
        <div className="print:mt-[8pt] print:grid print:grid-cols-[1fr_1fr] print:gap-[14pt] print:break-inside-avoid">
          <dl className={FACTS}>
            <dt className={DT_HEAD}>Access reviews</dt>
            <dt className={DT}>Responsible</dt>
            <dd className={DD}>
              {r.reviews.subscriptions_with_responsible} of {r.reviews.subscriptions} subscriptions
              {r.reviews.subscriptions_with_responsible > 0 &&
                r.reviews.without_responsible.length > 0 &&
                ` · nobody for ${placesText(r.reviews.without_responsible, 5)}`}
            </dd>
            <dt className={DT}>This month</dt>
            <dd className={DD}>
              {r.reviews.decided_this_period} of {r.reviews.items_this_period} grants decided in{" "}
              {r.reviews.rounds_this_period} {r.reviews.rounds_this_period === 1 ? "review" : "reviews"}
            </dd>
            <dt className={DT}>Last month</dt>
            <dd className={DD}>
              {r.reviews.last_period
                ? `${periodLabel(r.reviews.last_period)}, ${r.reviews.last_period_on_time ? "on time" : "not all on time"}`
                : "no earlier review"}
            </dd>
            <dt className={DT}>Changes</dt>
            <dd className={DD}>
              {r.reviews.todos_open} open, {r.reviews.todos_overdue} overdue, {r.reviews.todos_done} done
            </dd>
          </dl>
          <dl className={FACTS}>
            <dt className={DT_HEAD}>Changes and logging</dt>
            <dt className={DT}>Last {r.changes.days} days</dt>
            <dd className={DD}>
              {r.changes.given} given ({r.changes.powerful_given} powerful), {r.changes.removed} removed,{" "}
              {r.changes.unexplained} without a known reason
            </dd>
            <dt className={DT}>Daily check</dt>
            <dd className={DD}>{hoursText(r.changes.check_age_hours)} ago</dd>
            <dt className={DT}>Activity Log</dt>
            <dd className={DD}>
              {r.changes.subscriptions_collected_hourly} of {r.reviews.subscriptions} hourly, oldest{" "}
              {hoursText(r.changes.activity_gap_hours)} behind
            </dd>
          </dl>
        </div>

        <h3 className={H3}>Controls in detail</h3>
        <table className={TABLE}>
          <colgroup>
            <col style={{ width: "24%" }} />
            <col style={{ width: "11%" }} />
            <col />
          </colgroup>
          <tbody>
            {r.controls.map((c) => (
              <tr key={c.control} className={TR}>
                <td className={TD_DETAIL}>
                  <strong>A.{c.control}</strong> {c.name}
                </td>
                <td className={TD_DETAIL}>
                  <span className={STATUS_PILL[c.status]}>{STATUS_LABEL[c.status]}</span>
                </td>
                <td className={TD_DETAIL}>{c.evidence.join(" ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={cx("my-[1em]", MUTED, "print:mt-[8pt]")}>
          Annex: every grant ({r.grants}), with what it allows, how it is given and its advice, is in the CSV annex
          downloaded from the report page. Not counted above: {r.microsoft_apps} grants held by Microsoft&apos;s own
          apps. Azure never records reads or data access; Activity Log at management-group level is not collected.
        </p>
      </section>

      <style>{pageFooter(r)}</style>
    </article>
  );
}

function Tile(props: { label: string; value: number | null | undefined; before: number | null | undefined; note: string; tone?: "must" | "should" }) {
  const trend = deltaTrend(props.value, props.before);
  const delta = deltaText(props.value, props.before);
  return (
    <div className={cx(BOX, "print:border-[#cbd5e1] print:px-[8pt] print:py-[6pt]")}>
      <div className={MUTED}>{props.label}</div>
      <div className={props.tone ? BIG_TONE[props.tone] : BIG}>{props.value ?? "—"}</div>
      <div className={MUTED}>
        {props.note}
        {delta && <span className={trend ? TREND_TEXT[trend] : ""}> · {delta}</span>}
      </div>
    </div>
  );
}

function Action({ rec }: { rec: ReportRecommendation }) {
  const rows = rowsToPrint(groupGrants(rec.grants, place), rec.weight);
  return (
    <div className={REC_TONE[rec.weight]}>
      <p className="my-[1em] print:m-0 print:font-semibold print:break-after-avoid">
        <span className={WEIGHT_PILL[rec.weight]}>{WEIGHT_LABEL[rec.weight]}</span> {rec.sentence}
      </p>
      <p className={cx("my-[1em]", MUTED, "print:mx-0 print:mt-[1pt] print:mb-[2pt] print:break-after-avoid")}>
        {ruleId(rec.rule)} · {controlsText(rec.controls)} · {shownText(rows, rec.count)}
      </p>
      <table className={TABLE}>
        <colgroup>
          <col style={{ width: "42%" }} />
          <col style={{ width: "24%" }} />
          <col />
        </colgroup>
        <thead className={THEAD}>
          <tr className={TR}>
            <th scope="col" className={TH}>Who</th>
            <th scope="col" className={TH}>Role</th>
            <th scope="col" className={TH}>Where</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className={TR}>
              <td className={TD}>
                {row.who} <span className={MUTED}>{KIND[row.kind]}</span>
              </td>
              <td className={TD}>
                {row.role}
                {row.pim && <span className={MUTED}> · PIM</span>}
              </td>
              <td className={TD}>{placesText(row.where)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Privileged({ report: r }: { report: Report }) {
  const s = privilegedSummary(r.privileged);
  return (
    <div className={TR}>
      <h3 className={H3}>Privileged access, standing, high up</h3>
      <table className={cx(TABLE, "print:w-[75%]")}>
        <colgroup>
          <col />
          <col style={{ width: "16%" }} />
          <col style={{ width: "16%" }} />
          <col style={{ width: "16%" }} />
        </colgroup>
        <thead className={THEAD}>
          <tr className={TR}>
            <th scope="col" className={TH}>Can</th>
            <th scope="col" className={TH}>Management group</th>
            <th scope="col" className={TH}>Subscription</th>
            <th scope="col" className={TH}>Need action</th>
          </tr>
        </thead>
        <tbody>
          <tr className={TR}>
            <td className={TD}>Change who has access</td>
            <td className={cx(TD, NUM)}>{s.access.mg}</td>
            <td className={cx(TD, NUM)}>{s.access.sub}</td>
            <td className={cx(TD, NUM)}>{s.access.action}</td>
          </tr>
          <tr className={TR}>
            <td className={TD}>Read keys and passwords</td>
            <td className={cx(TD, NUM)}>{s.keys.mg}</td>
            <td className={cx(TD, NUM)}>{s.keys.sub}</td>
            <td className={cx(TD, NUM)}>{s.keys.action}</td>
          </tr>
        </tbody>
      </table>
      <p className={cx("my-[1em]", MUTED)}>
        {s.total} grants in all; those that need action are listed under Actions. The {s.fine} with nothing to do (mostly
        security services&apos; apps) are in the CSV annex.
        {r.break_glass.length > 0 && ` Break-glass identities, exempt by setting: ${r.break_glass.length}.`}
      </p>
    </div>
  );
}

/** Footer in the bottom margin of every printed page (CSS page margin boxes). */
function pageFooter(r: Report): string {
  const text = `RBAC Lens · ${r.scope_name} · generated ${formatDateTime(r.generated_at)}`;
  // line-height: page margin boxes inherit it from <html>, which Tailwind's reset sets to 1.5.
  const box = `font-family: Inter, system-ui, sans-serif; font-size: 7.5pt; line-height: normal; color: #64748b;`;
  return `@media print { @page { @bottom-left { content: ${cssString(text)}; ${box} } @bottom-right { content: "Page " counter(page) " of " counter(pages); ${box} } } }`;
}
