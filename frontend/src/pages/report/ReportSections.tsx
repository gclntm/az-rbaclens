import { Link } from "react-router-dom";

import type { AdviceWeight, Assessment, CapabilityLevel, ControlStatus, Report, ReportGrant, ReportRecommendation } from "@/api/client";
import { WEIGHT_LABEL, controlsText } from "@/components/advice/adviceUtils";
import { CAPTION, LEVEL_TAG } from "@/components/classes";
import { grantHref } from "@/pages/grants/grantUtils";
import { principalHref } from "@/pages/principals/principalUtils";
import { periodLabel } from "@/pages/reviews/reviewUtils";
import { formatDateTime, LEVEL_SHORT, scopeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import { compareNumbers, HOW_LABEL, hoursText, STATUS_LABEL, statusTrend } from "./reportUtils";

const KIND: Record<ReportGrant["kind"], string> = { person: "Person", group: "Group", app: "App", other: "Other" };
const TREND = { better: "↑ better", worse: "↓ worse", same: "= same" } as const;
// Grants shown per recommendation on screen (the printed report groups and shows more).
const SCREEN_ROWS = 5;

// Colours keep their ink on paper.
const EXACT = "print:[print-color-adjust:exact] print:[-webkit-print-color-adjust:exact]";
const MUTED_CAPTION = cx(CAPTION, "text-quaternary");
const SECTION = "mt-8 print:mt-[18pt] print:break-inside-auto";
const H2 = "mx-0 mt-0 mb-2 border-b border-b-secondary pb-1 text-[1.125rem] font-bold print:break-after-avoid";

// A table of the report; on a phone each row becomes a block.
const TABLE = "mx-0 my-2 w-full border-collapse text-[0.8125rem] print:text-[8.5pt]";
const THEAD = "max-[640px]:hidden";
const TR =
  "max-[640px]:block max-[640px]:border-b max-[640px]:border-b-secondary max-[640px]:px-0 max-[640px]:py-1.5 print:break-inside-avoid";
const TH = "border-b border-b-secondary px-2 py-1.5 text-left font-semibold text-tertiary";
const TD =
  "border-b border-b-secondary px-2 py-1.5 align-top [overflow-wrap:anywhere] " +
  "max-[640px]:block max-[640px]:border-none max-[640px]:border-current max-[640px]:px-0 max-[640px]:py-0.5";
const TD_NOWRAP = cx(TD, "whitespace-nowrap");

const FACTS =
  "mx-0 my-2 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 max-[640px]:grid-cols-[minmax(0,1fr)]";
const DT = "text-tertiary";
const DD = "m-0 [overflow-wrap:anywhere]";

const PILL = `inline-block whitespace-nowrap rounded-full px-2 py-px text-[0.6875rem] font-semibold ${EXACT}`;
const STATUS_PILL: Record<ControlStatus, string> = {
  met: `${PILL} bg-(--accent-green-subtle) text-success-primary`,
  partly: `${PILL} bg-(--accent-orange-subtle) text-warning-primary`,
  not_met: `${PILL} bg-(--accent-red-subtle) text-error-primary`,
};
const TREND_TEXT = { better: "text-success-primary", worse: "text-error-primary", same: "" } as const;

// An advice's weight as a pill, a dot, and a recommendation's left rule.
const WEIGHT = `flex-none whitespace-nowrap rounded-full px-2 py-px text-[0.6875rem] font-semibold uppercase tracking-[0.03em] ${EXACT}`;
const WEIGHT_PILL: Record<AdviceWeight, string> = {
  must: `${WEIGHT} bg-(--accent-red-subtle) text-error-primary`,
  should: `${WEIGHT} bg-(--accent-orange-subtle) text-warning-primary`,
  could: `${WEIGHT} bg-(--accent-blue-subtle) text-(--accent-blue)`,
  info: `${WEIGHT} bg-(--bg-input) text-tertiary`,
};
const DOT = `inline-block size-2 rounded-full align-middle ${EXACT}`;
const WEIGHT_DOT: Record<AdviceWeight, string> = {
  must: `${DOT} bg-(--accent-red)`,
  should: `${DOT} bg-(--accent-orange)`,
  could: `${DOT} bg-(--accent-blue)`,
  info: `${DOT} bg-(--text-muted)`,
};
const REC = "mx-0 my-4 border-l-3 pl-4 print:break-inside-avoid";
const REC_TONE: Record<AdviceWeight, string> = {
  must: `${REC} border-l-(--accent-red)`,
  should: `${REC} border-l-(--accent-orange)`,
  could: `${REC} border-l-(--accent-blue)`,
  info: `${REC} border-l-(--accent-green)`,
};
const REC_P = "mx-0 mt-0 mb-1";

// What a privileged grant allows, as a tinted chip.
const CHIP =
  "inline-flex max-w-full items-center gap-[5px] rounded-full px-2.5 py-0.5 text-[0.8125rem] leading-[1.5] font-medium";
const LEVEL_CHIP: Record<CapabilityLevel, string> = {
  access: `${CHIP} bg-(--accent-red-subtle) text-error-primary`,
  keys: `${CHIP} bg-(--accent-orange-subtle) text-warning-primary`,
  change: `${CHIP} bg-(--accent-blue-subtle) text-(--accent-blue)`,
  data: `${CHIP} bg-(--accent-purple-subtle) text-(--accent-purple)`,
  view: `${CHIP} bg-tertiary text-tertiary`,
  unknown: `${CHIP} bg-tertiary text-tertiary`,
};
const PLACE_TAG = cx(LEVEL_TAG, EXACT);

/** The report itself, in the order an assessment is read. */
export default function ReportSections({ report: r, compare }: { report: Report; compare: Assessment | null }) {
  const control = Object.fromEntries(r.controls.map((c) => [c.control, c]));
  return (
    <article className="max-w-[1000px] rounded-[8px] border border-secondary bg-primary px-[clamp(16px,4vw,48px)] py-6 print:hidden! print:max-w-none print:border-none print:bg-white print:p-0 print:text-black print:[&_a]:text-inherit print:[&_a]:no-underline">
      <header>
        <p className="m-0 text-[0.75rem] tracking-[0.06em] text-quaternary uppercase">Access assessment · ISO/IEC 27001:2022 Annex A</p>
        <h1 className="mx-0 my-1 text-[1.5rem] font-bold [overflow-wrap:anywhere]">{r.scope_id ? `${scopeLabel(r.scope_id, r.scope_name)}` : r.scope_name}</h1>
        <p className={cx("my-[1em]", MUTED_CAPTION)}>
          Generated {formatDateTime(r.generated_at)} · access as of{" "}
          {r.access_as_of ? formatDateTime(r.access_as_of) : "—"} · {r.identities} identities, {r.grants} grants
          {compare && ` · compared with the assessment of ${formatDateTime(compare.created_at)}`}
        </p>
      </header>

      <section className={SECTION}>
        <h2 className={H2}>1. Summary</h2>
        {r.summary.length === 0 ? (
          <p className="my-[1em]">No must-do or should-do advice: nothing stands out.</p>
        ) : (
          <>
            <p className="my-[1em]">What matters most, in order:</p>
            <ol className="my-[1em] list-decimal pl-10">
              {r.summary.map((s) => (
                <li key={s} className="mx-0 my-1">{s}</li>
              ))}
            </ol>
          </>
        )}
        <table className={TABLE}>
          <thead className={THEAD}>
            <tr className={TR}>
              <th scope="col" className={TH}>Control</th>
              <th scope="col" className={TH}>Status</th>
              {compare && <th scope="col" className={TH}>Since then</th>}
              <th scope="col" className={TH}>Based on</th>
            </tr>
          </thead>
          <tbody>
            {r.controls.map((c) => {
              const trend = compare ? statusTrend(c.status, compare.statuses[c.control]) : null;
              return (
                <tr key={c.control} className={TR}>
                  <td className={TD_NOWRAP}>
                    <strong>A.{c.control}</strong> {c.name}
                  </td>
                  <td className={TD}>
                    <span className={STATUS_PILL[c.status]}>{STATUS_LABEL[c.status]}</span>
                  </td>
                  {compare && <td className={cx(TD_NOWRAP, trend && TREND_TEXT[trend])}>{trend ? TREND[trend] : "—"}</td>}
                  <td className={cx(TD, CAPTION)}>{c.evidence.join(" ")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {compare && <Comparison report={r} compare={compare} />}
      </section>

      <section className={cx(SECTION, "print:break-before-page")}>
        <h2 className={H2}>2. Privileged access</h2>
        <Evidence control={control["8.2"]} />
        {r.privileged.length === 0 ? (
          <p className={cx("my-[1em]", CAPTION)}>Nobody can change access or read keys on a management group or subscription.</p>
        ) : (
          <GrantTable grants={r.privileged} showLevel />
        )}
        <p className={cx("my-[1em]", MUTED_CAPTION)}>
          Not counted: {r.microsoft_apps} grants held by Microsoft&apos;s own apps for Azure services.
          {r.break_glass.length > 0 && ` Break-glass identities, exempt by setting: ${r.break_glass.length}.`}
        </p>
      </section>

      <section className={SECTION}>
        <h2 className={H2}>3. Identities</h2>
        <Evidence control={control["5.16"]} />
        <SubList title="Identities that no longer exist in Entra ID" grants={r.identities_section.orphans} />
        <SubList title="Guests with power" grants={r.identities_section.guests} />
        <SubList title="Groups whose members can't be read" grants={r.identities_section.unknown_members} />
      </section>

      <section className={SECTION}>
        <h2 className={H2}>4. Least privilege</h2>
        <Evidence control={control["5.15"]} />
        <Evidence control={control["5.17"]} />
        {r.least_privilege.length === 0 ? (
          <p className={cx("my-[1em]", CAPTION)}>No grant is larger than needed, as far as the playbook can tell.</p>
        ) : (
          r.least_privilege.map((rec) => <Recommendation key={rec.rule} rec={rec} />)
        )}
      </section>

      <section className={SECTION}>
        <h2 className={H2}>5. Access reviews</h2>
        <Evidence control={control["5.18"]} />
        <dl className={FACTS}>
          <dt className={DT}>Responsible people</dt>
          <dd className={DD}>
            {r.reviews.subscriptions_with_responsible} of {r.reviews.subscriptions} subscriptions
            {r.reviews.without_responsible.length > 0 && ` · nobody for ${r.reviews.without_responsible.join(", ")}`}
          </dd>
          <dt className={DT}>This month</dt>
          <dd className={DD}>
            {r.reviews.rounds_this_period} reviews, {r.reviews.decided_this_period} of {r.reviews.items_this_period}{" "}
            grants decided
          </dd>
          <dt className={DT}>Last month</dt>
          <dd className={DD}>
            {r.reviews.last_period
              ? `${periodLabel(r.reviews.last_period)}: ${r.reviews.last_period_on_time ? "finished on time" : "not all finished on time"}`
              : "No earlier review"}
          </dd>
          <dt className={DT}>Changes to make</dt>
          <dd className={DD}>
            {r.reviews.todos_open} open, {r.reviews.todos_overdue} overdue, {r.reviews.todos_done} done
            {r.reviews.todos_avg_days_to_close !== null && ` (on average ${r.reviews.todos_avg_days_to_close} days to close)`}
          </dd>
        </dl>
      </section>

      <section className={SECTION}>
        <h2 className={H2}>6. Changes and evidence</h2>
        <Evidence control={control["8.15"]} />
        <dl className={FACTS}>
          <dt className={DT}>Last {r.changes.days} days</dt>
          <dd className={DD}>
            {r.changes.given} grants given ({r.changes.powerful_given} powerful), {r.changes.removed} removed,{" "}
            {r.changes.unexplained} without a known reason
          </dd>
          <dt className={DT}>Daily check</dt>
          <dd className={DD}>last finished {hoursText(r.changes.check_age_hours)} ago</dd>
          <dt className={DT}>Activity Log</dt>
          <dd className={DD}>
            {r.changes.subscriptions_collected_hourly} of {r.reviews.subscriptions} subscriptions collected hourly,
            oldest {hoursText(r.changes.activity_gap_hours)} behind
            {r.changes.subscriptions_not_collected.length > 0 &&
              ` · never collected: ${r.changes.subscriptions_not_collected.join(", ")}`}
          </dd>
        </dl>
        <p className={cx("my-[1em]", MUTED_CAPTION)}>
          Azure never records reads or data access, and Activity Log at management-group level is not collected.
        </p>
      </section>

      <section className={cx(SECTION, "print:break-before-page")}>
        <h2 className={H2}>7. Recommendations</h2>
        {r.recommendations.length === 0 ? (
          <p className="my-[1em]">Nothing to recommend.</p>
        ) : (
          r.recommendations.map((rec) => <Recommendation key={rec.rule} rec={rec} />)
        )}
      </section>

      <section className={SECTION}>
        <h2 className={H2}>8. Annex</h2>
        <p className={cx("my-[1em]", CAPTION)}>
          Every grant in this scope, with what it allows, how it is given and its advice, is in the CSV annex ({r.grants}{" "}
          rows), downloaded from this page.
        </p>
      </section>

    </article>
  );
}

function Evidence({ control: c }: { control?: Report["controls"][number] }) {
  if (!c) return null;
  return (
    <p className="mx-0 my-2 print:break-inside-avoid">
      <span className={STATUS_PILL[c.status]}>{STATUS_LABEL[c.status]}</span>{" "}
      <strong>
        A.{c.control} {c.name}.
      </strong>{" "}
      {c.evidence.join(" ")}
    </p>
  );
}

function GrantTable({ grants, showLevel = false }: { grants: ReportGrant[]; showLevel?: boolean }) {
  return (
    <table className={TABLE}>
      <thead className={THEAD}>
        <tr className={TR}>
          <th scope="col" className={TH}>Who</th>
          {showLevel && <th scope="col" className={TH}>Can</th>}
          <th scope="col" className={TH}>Role</th>
          <th scope="col" className={TH}>Where</th>
          <th scope="col" className={TH}>How</th>
          <th scope="col" className={TH}>Advice</th>
        </tr>
      </thead>
      <tbody>
        {grants.map((g) => (
          <tr key={g.grant_id} className={TR}>
            <td className={TD}>
              <Link to={principalHref(g.principal_id)}>{g.principal_name ?? g.principal_id}</Link>{" "}
              <span className={MUTED_CAPTION}>{KIND[g.kind]}</span>
            </td>
            {showLevel && (
              <td className={TD_NOWRAP}>
                <span className={LEVEL_CHIP[g.level]}>{g.level === "access" ? "Change access" : "Get keys"}</span>
              </td>
            )}
            <td className={TD}>
              <Link to={grantHref(g.grant_id)}>{g.role_name ?? "—"}</Link>
            </td>
            <td className={TD}>
              <span className={PLACE_TAG}>{LEVEL_SHORT[g.scope_level]}</span> {scopeLabel(g.scope, g.scope_name)}
            </td>
            <td className={TD_NOWRAP}>{HOW_LABEL[g.how]}</td>
            <td className={TD}>
              <span className={WEIGHT_DOT[g.advice_weight]} aria-hidden="true" /> {g.advice_title}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SubList({ title, grants }: { title: string; grants: ReportGrant[] }) {
  return (
    <div>
      <h3 className="mx-0 mt-4 mb-1 text-[0.9375rem] font-bold">
        {title}{" "}
        <span className={MUTED_CAPTION}>
          ({grants.length} {grants.length === 1 ? "grant" : "grants"})
        </span>
      </h3>
      {grants.length > 0 ? <GrantTable grants={grants} /> : <p className={cx("my-[1em]", CAPTION)}>None.</p>}
    </div>
  );
}

function Recommendation({ rec }: { rec: ReportRecommendation }) {
  return (
    <div className={REC_TONE[rec.weight]}>
      <p className={REC_P}>
        <span className={WEIGHT_PILL[rec.weight]}>{WEIGHT_LABEL[rec.weight]}</span> {rec.sentence}
      </p>
      <p className={cx(MUTED_CAPTION, REC_P)}>
        {rec.rule.split(" ")[0]} · ISO 27001: {controlsText(rec.controls)}
        {rec.count > SCREEN_ROWS && ` · first ${SCREEN_ROWS} of ${rec.count} shown, all in the annex`}
      </p>
      <GrantTable grants={rec.grants.slice(0, SCREEN_ROWS)} />
    </div>
  );
}

function Comparison({ report: r, compare }: { report: Report; compare: Assessment }) {
  const rows = compareNumbers(r.numbers, compare.numbers);
  return (
    <table className={TABLE}>
      <thead className={THEAD}>
        <tr className={TR}>
          <th scope="col" className={TH}>Key number</th>
          <th scope="col" className={TH}>Then</th>
          <th scope="col" className={TH}>Now</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className={cx(TR, row.better === null ? "" : row.better ? TREND_TEXT.better : TREND_TEXT.worse)}>
            <td className={TD}>{row.label}</td>
            <td className={TD}>{row.before ?? "—"}</td>
            <td className={TD}>
              {row.now ?? "—"}
              {row.better !== null && (row.better ? " ↓" : " ↑")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
