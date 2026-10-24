// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ReportPreset, type ReportSpec, type SavedReport } from "../api/client";
import { ResultTable } from "../components/ResultTable";
import { Builder } from "./reports/Builder";
import { EmailPanel, SchedulesView } from "./reports/EmailPanel";

type Period = "7" | "30" | "90" | "365" | "year" | "all" | "custom";
const PERIODS: [Period, string][] = [
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
  ["365", "Last 12 months"],
  ["year", "This year"],
  ["all", "All time"],
  ["custom", "Custom…"],
];

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function range(period: Period, from: string, to: string): { date_from?: string; date_to?: string } {
  const today = new Date();
  if (period === "all") return {};
  if (period === "custom") return { date_from: from || undefined, date_to: to || undefined };
  if (period === "year") return { date_from: `${today.getFullYear()}-01-01`, date_to: iso(today) };
  const start = new Date(today);
  start.setDate(start.getDate() - Number(period) + 1);
  return { date_from: iso(start), date_to: iso(today) };
}

type Current = { kind: "preset"; key: string } | { kind: "saved"; id: number } | { kind: "schedules" };

/** Reports: ready-made and saved reports for any period; customize, save, download. */
export function ReportsPage() {
  const queryClient = useQueryClient();
  const presets = useQuery({ queryKey: ["report-presets"], queryFn: api.reports.presets, staleTime: Infinity });
  const saved = useQuery({ queryKey: ["saved-reports"], queryFn: api.reports.saved });
  const [current, setCurrent] = useState<Current | null>(null);
  const [draft, setDraft] = useState<ReportSpec | null>(null);
  const [customizing, setCustomizing] = useState(false);
  const [savingAs, setSavingAs] = useState<{ name: string; description: string; shared: boolean } | null>(null);
  const [emailing, setEmailing] = useState(false);
  const [period, setPeriod] = useState<Period>("30");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [dateField, setDateField] = useState("created");

  const preset = current?.kind === "preset" ? presets.data?.find((p) => p.key === current.key) : current === null ? presets.data?.[0] : undefined;
  const savedReport: SavedReport | undefined = current?.kind === "saved" ? saved.data?.find((r) => r.id === current.id) : undefined;
  const base = savedReport ?? preset;
  const title = savedReport?.name ?? preset?.title ?? "";
  const description = savedReport ? savedReport.description : (preset?.description ?? "");
  const baseSpec: ReportSpec | undefined = base?.spec;
  const datasetName = (draft ?? baseSpec)?.dataset ?? "tickets";
  const dataset = useQuery({ queryKey: ["report-dataset", datasetName], queryFn: () => api.reports.dataset(datasetName), staleTime: Infinity });
  // The date the period applies to: the report's own, else the dataset's first.
  const dates = dataset.data?.dates ?? [];
  const effectiveDate = dates.some((d) => d.key === dateField) ? dateField : (dates[0]?.key ?? dateField);
  const spec: ReportSpec | null = baseSpec ? { ...(draft ?? baseSpec), date_field: effectiveDate, ...range(period, from, to) } : null;
  const result = useQuery({ queryKey: ["report", spec], queryFn: () => api.reports.run(spec!), enabled: spec !== null && dataset.isSuccess });
  const fileName = (savedReport?.name ?? preset?.key ?? "report").replace(/[^\w-]+/g, "-").toLowerCase();
  const csv = useMutation({ mutationFn: () => api.reports.downloadCsv(spec!, `${fileName}.csv`) });
  const pdf = useMutation({ mutationFn: () => api.reports.downloadPdf({ spec: spec!, title, description }) });

  const refreshSaved = () => queryClient.invalidateQueries({ queryKey: ["saved-reports"] });
  const saveAs = useMutation({
    mutationFn: (v: { name: string; description: string; shared: boolean }) => api.reports.save({ ...v, spec: { ...(draft ?? baseSpec)!, date_field: effectiveDate } }),
    onSuccess: (r) => {
      setSavingAs(null);
      setDraft(null);
      setCurrent({ kind: "saved", id: r.id });
    },
    onSettled: refreshSaved,
  });
  const update = useMutation({
    mutationFn: () => api.reports.update(savedReport!.id, { name: savedReport!.name, description: savedReport!.description, shared: savedReport!.shared, spec: { ...draft!, date_field: effectiveDate } }),
    onSuccess: () => setDraft(null),
    onSettled: refreshSaved,
  });
  const remove = useMutation({
    mutationFn: () => api.reports.remove(savedReport!.id),
    onSuccess: () => {
      setCurrent(null);
      setDraft(null);
    },
    onSettled: refreshSaved,
  });

  function choose(next: Current, spec: ReportSpec) {
    setCurrent(next);
    setDraft(null);
    setCustomizing(false);
    setSavingAs(null);
    setEmailing(false);
    setDateField(spec.date_field ?? "created");
  }

  const byGroup = new Map<string, ReportPreset[]>();
  for (const p of presets.data ?? []) byGroup.set(p.group, [...(byGroup.get(p.group) ?? []), p]);
  const error = result.error ?? csv.error ?? pdf.error ?? saveAs.error ?? update.error ?? remove.error;
  const showingSchedules = current?.kind === "schedules";

  return (
    <section className="analysis-workspace report-workspace">
      <div className="page-head">
        <WorkspaceTitle title="Reports" area="SERVICE INSIGHTS" />
      </div>
      {presets.error && <p className="error" role="alert">{presets.error.message}</p>}
      {saved.error && <p className="error" role="alert">Saved reports could not be loaded: {saved.error.message}</p>}
      {presets.isPending && <p className="muted" role="status">Loading report library…</p>}
      {presets.isSuccess && saved.isSuccess && !presets.data.length && !saved.data.length && <p className="card muted" role="status">No reports are available yet. Scheduled reports remain accessible from the library.</p>}
      <div className="admin analysis-layout">
        <nav className="admin-list report-list" aria-label="Reports">
          <ul>
            <li>
              <button type="button" aria-current={showingSchedules} onClick={() => setCurrent({ kind: "schedules" })}>
                Scheduled reports
              </button>
            </li>
          </ul>
          {(saved.data?.length ?? 0) > 0 && (
            <div>
              <div className="lbl">Saved reports</div>
              <ul>
                {saved.data!.map((r) => (
                  <li key={r.id}>
                    <button type="button" aria-current={current?.kind === "saved" && current.id === r.id} onClick={() => choose({ kind: "saved", id: r.id }, r.spec)}>
                      {r.name}
                      <span className="muted small">{r.shared ? " · shared" : " · just me"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {[...byGroup].map(([group, list]) => (
            <div key={group}>
              <div className="lbl">{group}</div>
              <ul>
                {list.map((p) => (
                  <li key={p.key}>
                    <button type="button" aria-current={base === p} onClick={() => choose({ kind: "preset", key: p.key }, p.spec)}>
                      {p.title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        {showingSchedules && <SchedulesView />}
        {!showingSchedules && base && spec && (
          <section className="card stack analysis-detail" style={{ margin: 0 }} aria-label={title}>
            <header className="analysis-detail-head">
              <p className="workspace-area">{savedReport ? "SAVED REPORT" : "REPORT WORKSPACE"}</p>
              <h2 style={{ margin: 0 }}>
                {title}
                {draft && <span className="muted small"> (changed)</span>}
              </h2>
              {description && (
                <p className="muted" style={{ margin: "4px 0 0" }}>
                  {description}
                </p>
              )}
              {savedReport?.owner && (
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  Saved by {savedReport.owner.display_name}
                  {savedReport.shared ? ", shared with everyone" : ""}
                </p>
              )}
            </header>
            <div className="row-gap report-controls" role="group" aria-label="Report controls">
              <label className="inline small">
                {dataset.data?.label ?? "Tickets"}
                <select value={effectiveDate} onChange={(e) => setDateField(e.target.value)} aria-label="Which date">
                  {dates.map((d) => (
                    <option key={d.key} value={d.key}>
                      {d.label.toLowerCase()}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline small">
                <select value={period} onChange={(e) => setPeriod(e.target.value as Period)} aria-label="Period">
                  {PERIODS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {period === "custom" && (
                <>
                  <label className="inline small">
                    From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
                  </label>
                  <label className="inline small">
                    to <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
                  </label>
                </>
              )}
              <span className="spacer" />
              <button className="btn2" type="button" aria-expanded={customizing} onClick={() => setCustomizing(!customizing)}>
                {customizing ? "Done customizing" : "Customize"}
              </button>
              <button className="btn2" type="button" disabled={!result.data || pdf.isPending} onClick={() => pdf.mutate()}>
                Download PDF
              </button>
              <button className="btn2" type="button" disabled={!result.data || csv.isPending} onClick={() => csv.mutate()}>
                Download CSV
              </button>
              <button className="btn2" type="button" aria-expanded={emailing} onClick={() => setEmailing(!emailing)}>
                Email or schedule…
              </button>
            </div>
            {emailing && (
              <EmailPanel
                target={{
                  title,
                  description,
                  spec,
                  presetKey: preset?.key,
                  savedReportId: savedReport?.id,
                  unsaved: draft !== null,
                }}
                onClose={() => setEmailing(false)}
                onScheduled={() => {
                  setEmailing(false);
                  setCurrent({ kind: "schedules" });
                }}
              />
            )}
            {customizing && dataset.data && <Builder dataset={dataset.data} spec={draft ?? baseSpec!} onChange={setDraft} />}
            {(draft || savedReport?.can_edit) && savingAs === null && (
              <div className="row-gap">
                {draft && savedReport?.can_edit && (
                  <button className="btn" type="button" disabled={update.isPending} onClick={() => update.mutate()}>
                    Save changes
                  </button>
                )}
                {draft && (
                  <button className="btn2" type="button" onClick={() => setSavingAs({ name: savedReport ? `${savedReport.name} (copy)` : title, description: "", shared: false })}>
                    Save as new report…
                  </button>
                )}
                {draft && (
                  <button className="linkish small" type="button" onClick={() => setDraft(null)}>
                    Undo changes
                  </button>
                )}
                {savedReport?.can_edit && !draft && (
                  <button
                    className="linkish small"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Delete the saved report "${savedReport.name}"?`)) remove.mutate();
                    }}
                  >
                    Delete this report
                  </button>
                )}
              </div>
            )}
            {savingAs && (
              <form
                className="stack card"
                style={{ margin: 0, gap: 8 }}
                aria-label="Save report"
                onSubmit={(e) => {
                  e.preventDefault();
                  saveAs.mutate(savingAs);
                }}
              >
                <label className="field">
                  Name
                  <input required autoFocus maxLength={200} value={savingAs.name} onChange={(e) => setSavingAs({ ...savingAs, name: e.target.value })} />
                </label>
                <label className="field">
                  Description <span className="muted small">(optional)</span>
                  <input maxLength={2000} value={savingAs.description} onChange={(e) => setSavingAs({ ...savingAs, description: e.target.value })} />
                </label>
                <label className="inline">
                  <input type="checkbox" checked={savingAs.shared} onChange={(e) => setSavingAs({ ...savingAs, shared: e.target.checked })} /> Share with everyone
                </label>
                <div className="row-gap">
                  <button className="btn2" type="button" onClick={() => setSavingAs(null)}>
                    Cancel
                  </button>
                  <button className="btn" type="submit" disabled={saveAs.isPending}>
                    Save report
                  </button>
                </div>
              </form>
            )}
            {error && <p className="error">{error.message}</p>}
            {result.isFetching && !result.data && <p className="muted" role="status">Running…</p>}
            {result.data && <section className="analysis-results" aria-label="Report results"><header><h3>Results</h3><span className="muted small" role="status">{result.data.rows.length} displayed row(s)</span></header><ResultTable result={result.data} /></section>}
            {result.data?.truncated && <p className="muted small">Showing the first {result.data.rows.length} rows. Narrow the period or raise the row limit (Customize) to see more.</p>}
          </section>
        )}
      </div>
    </section>
  );
}
