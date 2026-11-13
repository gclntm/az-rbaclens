import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import Button from "@/components/Button";
import { api, type Assessment, type Report, type ScopeNode } from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import { useToast } from "@/components/Toast";
import { CARD, EMPTY_STATE, FORM_FIELD } from "@/components/classes";
import { PageHeader, SectionCard } from "@/components/PageTemplate";
import { isRestricted, useMe } from "@/hooks/useMe";
import { scopeOptions } from "@/pages/jobs/jobUtils";
import { formatDateTime } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import ReportPrint from "./ReportPrint";
import ReportSections from "./ReportSections";
import "./report.css";

// A field of the tools row: grows from 240px, at most 420px wide (its select fills it).
const SCOPE_FIELD = cx(FORM_FIELD, "min-w-0 max-w-[420px] flex-[1_1_240px]");

/**
 * The assessment report: what matters in a place, mapped to ISO/IEC 27001:2022.
 * Route: /report?scope=<ARM path> (none = the whole tenant, admins only). Prints to PDF in a
 * shorter layout of its own (ReportPrint).
 */
export default function ReportPage() {
  const me = useMe();
  const admin = !isRestricted(me);
  const { showToast } = useToast();
  const [sp, setSp] = useSearchParams();
  const scope = sp.get("scope");
  const compareId = Number(sp.get("compare")) || null;
  const [tree, setTree] = useState<ScopeNode[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [saved, setSaved] = useState<Assessment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.scopes
      .tree()
      .then(setTree)
      .catch(() => setTree([]));
  }, []);

  useEffect(() => {
    if (me === null) return; // still loading who this is
    if (!scope && !admin) return; // pick a place first
    setReport(null);
    setError(null);
    api.report
      .get(scope)
      .then(setReport)
      .catch((e: Error) => setError(e.message));
    api.report
      .assessments(scope)
      .then(setSaved)
      .catch(() => setSaved([]));
  }, [scope, admin, me]);

  const places = useMemo(() => scopeOptions(tree, []), [tree]);
  const compare = saved.find((a) => a.id === compareId) ?? null;

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "scope") next.delete("compare");
    setSp(next, { replace: true });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const a = await api.report.save(scope, note.trim() || null);
      setSaved([a, ...saved]);
      setNote("");
      showToast("Assessment saved", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        className="print:hidden!"
        title="Report"
        sub={
          <>
            How access is governed, control by control, for the whole tenant or one place: to print, save as PDF, or
            keep as an assessment to compare with later.
          </>
        }
      />

      <SectionCard className="print:hidden!" icon="report" title="What to assess" hint="A place and, if one was saved, an earlier assessment to compare with.">
        <div className="mb-4 flex flex-wrap items-end gap-x-4 gap-y-2 print:hidden!">
          <label className={SCOPE_FIELD}>
            <span>Assess</span>
            <select className="font-normal tracking-normal w-full" value={scope ?? ""} onChange={(e) => setParam("scope", e.target.value || null)}>
              {admin ? <option value="">The whole tenant</option> : <option value="">Pick one of your places…</option>}
              {places.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          {saved.length > 0 && (
            <label className={SCOPE_FIELD}>
              <span>Compare with</span>
              <select className="font-normal tracking-normal w-full" value={compareId ?? ""} onChange={(e) => setParam("compare", e.target.value || null)}>
                <option value="">No comparison</option>
                {saved.map((a) => (
                  <option key={a.id} value={a.id}>
                    {formatDateTime(a.created_at)}
                    {a.note ? ` · ${a.note}` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" type="button" onClick={() => window.print()} disabled={!report}>
              Print or save as PDF
            </Button>
            <DownloadLink href={api.report.annexUrl(scope)}>
              Annex (CSV)
            </DownloadLink>
          </div>
        </div>
      </SectionCard>

      {!scope && !admin && <p className={cx("my-[1em]", CARD, EMPTY_STATE, "px-0")}>Pick one of your places to assess.</p>}
      {error && <p className="my-[1em] text-error-primary">Could not build the report: {error}</p>}
      {!report && !error && (scope || admin) && <p className="my-[1em] text-quaternary">Building the report…</p>}
      {report && <ReportSections report={report} compare={compare} />}
      {/* Printed instead of the sections: compared with the chosen assessment, else the latest saved one. */}
      {report && <ReportPrint report={report} baseline={compare ?? saved[0] ?? null} />}

      {report && admin && (
        <SectionCard
          className="print:hidden!"
          icon="history"
          title="Save this assessment"
          hint={`Keeps the control statuses and key numbers as they are now, read-only, so a later report can show what changed.${saved.length > 0 ? ` ${saved.length} saved for this place.` : ""}`}
        >
          <form className="mt-4 max-w-[1000px] print:hidden!" onSubmit={save}>
            <div className="flex flex-wrap gap-2">
              <input
                className="font-normal tracking-normal min-w-0 flex-[1_1_260px] leading-[inherit]"
                placeholder="Note (optional), e.g. Before the clean-up"
                aria-label="Note for this assessment"
                value={note}
                maxLength={2000}
                onChange={(e) => setNote(e.target.value)}
              />
              <Button size="sm" type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save assessment"}
              </Button>
            </div>
          </form>
        </SectionCard>
      )}
    </div>
  );
}
