// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type TrackitChoices, type TrackitJob } from "../../api/client";

type Field = TrackitJob["fields"][number];
type FieldChoice = "keep" | "skip" | "rename";

/** The same choices as the server normalises them, in a stable order, to compare with a run's. */
function canonical(c: Omit<TrackitChoices, "apply" | "confirm">) {
  const sorted = (o: Record<string, string> = {}) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
  return JSON.stringify([c.queue || null, sorted(c.groups ?? {}), sorted(c.labels ?? {}), [...(c.skip ?? [])].sort(), c.departments ?? "match"]);
}

function size(bytes: number) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Track-It! history: upload a bundle once, choose where things go, dry run, then import. */
export function TrackitImport() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("trackit");
  const [file, setFile] = useState<File | null>(null);
  const [queue, setQueue] = useState("");
  const [groups, setGroups] = useState<Record<string, string>>({});
  const [fieldChoice, setFieldChoice] = useState<Record<string, FieldChoice>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [departments, setDepartments] = useState<TrackitChoices["departments"]>("match");
  const [restored, setRestored] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const job = useQuery({
    queryKey: ["trackit-import", jobId], queryFn: () => api.imports.trackit(jobId!), enabled: !!jobId, retry: false,
    refetchInterval: query => (query.state.data?.state === "running" ? 2000 : false),
  });
  const setJob = (id: string | null) => setParams(prev => { const next = new URLSearchParams(prev); if (id) next.set("trackit", id); else next.delete("trackit"); return next; }, { replace: true });

  const upload = useMutation({
    mutationFn: () => api.imports.trackitUpload(file!),
    onSuccess: data => {
      client.setQueryData(["trackit-import", data.id], data);
      setJob(data.id); setFile(null); setQueue(""); setGroups({}); setFieldChoice({}); setNames({}); setDepartments("match"); setConfirm(false); setRestored(data.id);
      if (picker.current) picker.current.value = "";
    },
  });
  const choices: Omit<TrackitChoices, "apply" | "confirm"> = useMemo(() => {
    const labels: Record<string, string> = {};
    const skip: string[] = [];
    for (const [column, choice] of Object.entries(fieldChoice)) {
      if (choice === "skip") skip.push(column);
      if (choice === "rename" && names[column]?.trim()) labels[column] = names[column].trim();
    }
    return { queue: queue || null, groups: Object.fromEntries(Object.entries(groups).filter(([, v]) => v)), labels, skip, departments };
  }, [queue, groups, fieldChoice, names, departments]);
  const run = useMutation({
    mutationFn: (apply: boolean) => api.imports.trackitRun(jobId!, { ...choices, apply, confirm: apply && confirm }),
    onSuccess: data => { client.setQueryData(["trackit-import", jobId], data); setConfirm(false); },
  });
  const discard = useMutation({
    mutationFn: () => api.imports.trackitDiscard(jobId!),
    onSuccess: () => { client.removeQueries({ queryKey: ["trackit-import", jobId] }); setJob(null); },
  });

  const data = job.data;
  // Coming back to a staged bundle (a reload, or later): show the choices its last run used.
  useEffect(() => {
    if (!data || restored === data.id) return;
    setRestored(data.id);
    const used = data.used;
    if (!used) return;
    setQueue(used.queue ?? ""); setGroups(used.groups ?? {}); setDepartments(used.departments ?? "match");
    setFieldChoice({ ...Object.fromEntries((used.skip ?? []).map(c => [c, "skip" as FieldChoice])), ...Object.fromEntries(Object.keys(used.labels ?? {}).map(c => [c, "rename" as FieldChoice])) });
    setNames(used.labels ?? {});
  }, [data, restored]);
  const running = data?.state === "running";
  const imported = data?.state === "done" && data.apply;
  const sameChoices = !!data?.used && canonical(choices) === canonical({ ...data.used });
  const dryRunDone = data?.state === "done" && !data.apply && sameChoices;
  const busy = upload.isPending || run.isPending || discard.isPending || running;
  useEffect(() => {
    if (imported) client.invalidateQueries({ predicate: q => ["tickets", "views", "users"].includes(String(q.queryKey[0])) });
  }, [imported, client]);
  const active = queues.data?.filter(q => q.is_active) ?? [];
  const groupField = data?.fields.find(f => f.column === "OPENED_GROUP");
  const deptField = data?.fields.find(f => f.column === "DEPT");
  const otherFields = data?.fields.filter(f => f.column !== "OPENED_GROUP" && f.column !== "DEPT") ?? [];
  const lock = busy || imported;
  const queueSelect = (value: string, onChange: (v: string) => void, blank: string, label: string) => (
    <select aria-label={label} value={value} disabled={lock} onChange={e => onChange(e.target.value)}>
      <option value="">{blank}</option>
      {active.map(q => <option key={q.id} value={q.name}>{q.name}</option>)}
    </select>
  );
  const fieldRow = (f: Field) => (
    <tr key={f.column}>
      <td><code>{f.column}</code><br /><span className="muted small">{f.name}</span></td>
      <td>{f.used} of {f.total} tickets, {f.distinct} value{f.distinct === 1 ? "" : "s"}<br /><span className="muted small">{f.top.map(([v, n]) => `${v} (${n})`).join("; ")}</span></td>
      <td>
        <select aria-label={`${f.column}: keep, skip or rename`} value={fieldChoice[f.column] ?? "keep"} disabled={lock}
          onChange={e => setFieldChoice(c => ({ ...c, [f.column]: e.target.value as FieldChoice }))}>
          <option value="keep">Keep as {f.becomes}</option>
          <option value="rename">Keep, named…</option>
          <option value="skip">Leave out</option>
        </select>
        {fieldChoice[f.column] === "rename" && <input aria-label={`New name for ${f.column}`} value={names[f.column] ?? ""} disabled={lock} placeholder="e.g. Building"
          onChange={e => setNames(n => ({ ...n, [f.column]: e.target.value }))} />}
      </td>
    </tr>
  );

  return <section className="card stack" aria-label="Track-It! ticket import">
    <p className="workspace-area">DATA MIGRATION / TRACK-IT!</p>
    <h2>Bring your Track-It! history into Layer 8</h2>
    <p>Import a bundle made by the Track-It! export script: tickets, notes, work orders (as child tickets), attachments, solutions (as knowledge base articles) and assets (as devices). Do a dry run first: nothing is saved until you import. No emails, routing rules or surveys run.</p>
    <details><summary>Prepare a Track-It! export</summary><div className="stack">
      <p>On the Track-It! server, or a Windows PC that can reach its SQL Server, run <code>tools/trackit-export/trackit-export.ps1</code> from the matching Layer 8 source. It reads the database read-only, as you (Windows sign-in), and never reads passwords.</p>
      <pre className="code import-command">{".\\trackit-export.ps1 -Server 'TRACKITSRV\\TRACKIT' -Out D:\\trial.zip -Since (Get-Date).AddDays(-10)\n.\\trackit-export.ps1 -Server 'TRACKITSRV\\TRACKIT' -Out D:\\trackit.zip"}</pre>
      <p>Start with a trial (<code>-Since</code>), then the whole history. Importing the same bundle again updates what it imported before, without duplicates. The bundle holds your real data: copy it here on site, not by email. The guide is <code>docs/trackit-import.md</code> in the source.</p>
    </div></details>

    {!jobId && <>
      <label className="field">Track-It! export bundle (.zip)<input ref={picker} type="file" accept=".zip,application/zip" disabled={busy}
        onChange={e => { upload.reset(); setFile(e.target.files?.[0] ?? null); }} /></label>
      <p className="muted small">Up to 1 GiB. The bundle is kept on the server only for this import (never in backups) and deleted once imported, when you discard it, or after 24 hours.</p>
      <div><button className="btn2" type="button" disabled={busy || !file} onClick={() => upload.mutate()}>{upload.isPending ? "Uploading and checking…" : "Upload and check"}</button></div>
      {upload.error && <p className="error" role="alert">{upload.error.message}</p>}
    </>}

    {jobId && job.error && <p className="error" role="alert">{job.error.message} <button className="btn2 small" type="button" onClick={() => setJob(null)}>Start again</button></p>}

    {data && <>
      <p>Export of <strong>{data.database ?? "Track-It!"}</strong>{data.server && <> on <strong>{data.server}</strong></>}{data.exported_at && <>, made {data.exported_at.slice(0, 16).replace("T", " ")}</>}
        {data.since && <>, tickets since {data.since.slice(0, 10)}</>} ({size(data.size)}).</p>

      <h3>Where tickets go</h3>
      <label className="field">Queue for tickets{queueSelect(queue, setQueue, "The default queue", "Queue for tickets")}</label>
      {groupField && <>
        <p className="muted small">Track-It! groups that opened tickets. A group named like one of your queues goes there by itself; choose others here.</p>
        <table className="table"><thead><tr><th>Track-It! group</th><th>Tickets</th><th>Queue</th></tr></thead><tbody>
          {groupField.top.map(([group, n]) => <tr key={group}><td>{group}</td><td>{n}</td>
            <td>{queueSelect(groups[group] ?? "", v => setGroups(g => ({ ...g, [group]: v })), "Same name, else the queue above", `Queue for Track-It! group ${group}`)}</td></tr>)}
        </tbody></table>
        {groupField.distinct > groupField.top.length && <p className="muted small">Showing the {groupField.top.length} commonest of {groupField.distinct} groups; the others follow the rule above, or use the command line's <code>--group</code>.</p>}
      </>}
      {deptField && <label className="field">Departments ({deptField.used} tickets, {deptField.distinct} departments)
        <select value={departments} disabled={lock} onChange={e => setDepartments(e.target.value as TrackitChoices["departments"])}>
          <option value="match">Match your existing department choices only (others kept as an imported detail)</option>
          <option value="add">Add missing departments as choices</option>
          <option value="skip">Leave departments out</option>
        </select></label>}

      {!!otherFields.length && <>
        <h3>Other Track-It! fields</h3>
        <p className="muted small">Nothing is added to your forms or lists: kept fields show as imported details on imported tickets only.</p>
        <table className="table"><thead><tr><th>Field</th><th>Used</th><th>On imported tickets</th></tr></thead><tbody>{otherFields.map(fieldRow)}</tbody></table>
      </>}

      {(run.error || discard.error) && <p className="error" role="alert">{run.error?.message || discard.error?.message}</p>}
      {running && <p role="status">{data.apply ? "Importing" : "Dry run"} in progress. This can take several minutes for a long history; you can leave this page and come back.</p>}
      {data.state === "failed" && <p className="error" role="alert">The {data.apply ? "import" : "dry run"} stopped: {data.error}</p>}

      {data.report && !running && <div className="stack">
        <h3>{data.apply ? "Imported" : "Dry run result"}</h3>
        <pre className="code import-report">{data.report.lines.join("\n")}</pre>
      </div>}

      {!imported && <div className="stack">
        <div className="row-actions">
          <button className="btn2" type="button" disabled={busy} onClick={() => run.mutate(false)}>{dryRunDone ? "Dry run again" : "Dry run"}</button>
          <button className="btn2" type="button" disabled={busy} onClick={() => discard.mutate()}>Discard bundle</button>
        </div>
        {dryRunDone && <>
          <label className="inline"><input type="checkbox" checked={confirm} disabled={busy} onChange={e => setConfirm(e.target.checked)} />I reviewed this dry run and have a current backup. Import this bundle with these choices.</label>
          <div><button className="btn" type="button" disabled={!confirm || busy} onClick={() => run.mutate(true)}>Import</button> <Link to="/settings/backup">Backups</Link></div>
        </>}
        {data.state === "done" && !data.apply && !sameChoices && <p className="muted small">The choices changed since this dry run: run it again before importing.</p>}
      </div>}

      {imported && <p>Done: the bundle was deleted from the server. <Link to="/?q=Track-It&scope=all">Find imported tickets (all statuses)</Link> · <button className="btn2 small" type="button" onClick={() => discard.mutate()}>Import another bundle</button></p>}
    </>}
  </section>;
}
