import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Me, type Script, type ScriptIn, type ScriptParam } from "../../api/client";
import { ResultTable } from "../../components/ResultTable";
import { ParamField } from "./ParamField";
import { RunActionForm, RunList } from "./Runs";
import { SchedulesPane } from "./Schedules";
import { can } from "../../permissions";

const LANGUAGE_LABEL: Record<string, string> = { sql: "SQL", powershell: "PowerShell", bash: "bash", python: "Python", eos: "Arista EOS" };
const BLANK: ScriptIn = { name: "", description: "", kind: "query", language: "sql", high_risk: false, agents_can_run: false, is_active: true, body: "", parameters: [], note: "" };

type Pane = { kind: "script"; id: number; tab: "run" | "runs" | "schedules" | "edit" | "versions" } | { kind: "new" } | { kind: "runs" } | null;

/** The script library: read-only queries (reports, dynamic groups) and actions that run on devices. */
export function ScriptsPage({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ["scripts"], queryFn: api.scripts.list });
  const [pane, setPane] = useState<Pane>(null);
  const waiting = useQuery({ queryKey: ["runs", { status: "awaiting_approval" }], queryFn: () => api.runs.list({ status: "awaiting_approval" }), enabled: me.role === "admin" });
  const examples = useMutation({ mutationFn: api.scripts.addExamples, onSettled: () => queryClient.invalidateQueries({ queryKey: ["scripts"] }) });
  const writes = can(me, "scripts_manage");
  const current = pane?.kind === "script" ? list.data?.find((s) => s.id === pane.id) : pane === null ? list.data?.[0] : undefined;
  const tab = pane?.kind === "script" ? pane.tab : "run";
  const groups: [string, Script[]][] = [
    ["Queries", (list.data ?? []).filter((s) => s.kind === "query")],
    ["Actions", (list.data ?? []).filter((s) => s.kind === "action")],
  ];

  return (
    <section className="analysis-workspace script-workspace">
      <div className="page-head row-gap">
        <WorkspaceTitle title="Scripts" area="AUTOMATION" />
        {writes && (
          <>
            <button className="btn2" type="button" disabled={examples.isPending} onClick={() => examples.mutate()}>
              Add example queries
            </button>
            <button className="btn" type="button" onClick={() => setPane({ kind: "new" })}>
              New script
            </button>
          </>
        )}
      </div>
      {examples.data && <p className="muted small">{examples.data.length ? `Added: ${examples.data.join(", ")}.` : "The examples are all here already."}</p>}
      {list.error && <p className="error">{list.error.message}</p>}
      <div className="admin analysis-layout">
        <nav className="admin-list report-list" aria-label="Scripts">
          <ul>
            <li>
              <button type="button" aria-current={pane?.kind === "runs"} onClick={() => setPane({ kind: "runs" })}>
                All runs
                {(waiting.data?.length ?? 0) > 0 && <span className="chip a"> {waiting.data!.length} to approve</span>}
              </button>
            </li>
          </ul>
          {groups.map(([label, items]) => (
            <div key={label}>
              <div className="lbl">{label}</div>
              {items.length === 0 && <p className="muted small">None yet.</p>}
              <ul>
                {items.map((s) => (
                  <li key={s.id}>
                    <button type="button" aria-current={current?.id === s.id && pane?.kind !== "new"} onClick={() => setPane({ kind: "script", id: s.id, tab: "run" })}>
                      {s.name}
                      <span className="muted small">
                        {" "}
                        · {LANGUAGE_LABEL[s.language]}
                        {!s.is_active && " · off"}
                        {s.high_risk && " · high risk"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        {pane?.kind === "new" && <Editor key="new" initial={BLANK} onSaved={(s) => setPane({ kind: "script", id: s.id, tab: "run" })} onCancel={() => setPane(null)} />}
        {pane?.kind === "runs" && (
          <section className="card stack" style={{ margin: 0 }} aria-label="All runs">
            <h2 style={{ margin: 0 }}>All runs</h2>
            {(waiting.data?.length ?? 0) > 0 && (
              <>
                <div className="lbl" style={{ margin: 0 }}>
                  Waiting for approval
                </div>
                <RunList filter={{ status: "awaiting_approval" }} showScript />
                <div className="lbl" style={{ margin: 0 }}>
                  Everything
                </div>
              </>
            )}
            <RunList filter={{}} showScript />
          </section>
        )}
        {pane?.kind !== "new" && pane?.kind !== "runs" && current && (
          <section className="card stack analysis-detail" style={{ margin: 0 }} aria-label={current.name}>
            <header className="analysis-detail-head">
              <p className="workspace-area">{current.kind === "query" ? "QUERY WORKSPACE" : "ACTION WORKSPACE"}</p>
              <h2 style={{ margin: 0 }}>{current.name}</h2>
              <p className="muted small" style={{ margin: "4px 0 0" }}>
                {current.kind === "query" ? "Query" : "Action"} in {LANGUAGE_LABEL[current.language]} · version {current.current_version}
                {current.updated_by && ` by ${current.updated_by.display_name}`}
                {current.agents_can_run ? " · agents can run it" : " · admins only"}
              </p>
              {current.description && <p style={{ margin: "8px 0 0" }}>{current.description}</p>}
              <div className="row-gap analysis-badges" aria-label="Script state">
                <span className={`chip ${current.can_run ? "g" : "n"}`}>{current.can_run ? "Run permitted" : "Run unavailable"}</span>
                {current.high_risk && <span className="chip a">High risk</span>}
                {!current.is_active && <span className="chip n">Inactive</span>}
              </div>
            </header>
            <div className="views" role="group" aria-label="Show">
              <button type="button" aria-pressed={tab === "run"} onClick={() => setPane({ kind: "script", id: current.id, tab: "run" })}>
                Details
              </button>
              {current.kind === "action" && (
                <button type="button" aria-pressed={tab === "runs"} onClick={() => setPane({ kind: "script", id: current.id, tab: "runs" })}>
                  Runs
                </button>
              )}
              <button type="button" aria-pressed={tab === "schedules"} onClick={() => setPane({ kind: "script", id: current.id, tab: "schedules" })}>
                Schedules
              </button>
              {writes && (
                <button type="button" aria-pressed={tab === "edit"} onClick={() => setPane({ kind: "script", id: current.id, tab: "edit" })}>
                  Edit
                </button>
              )}
              <button type="button" aria-pressed={tab === "versions"} onClick={() => setPane({ kind: "script", id: current.id, tab: "versions" })}>
                Versions
              </button>
            </div>
            {tab === "run" && (
              <RunPane key={`${current.id}-${current.current_version}`} script={current} onStarted={() => setPane({ kind: "script", id: current.id, tab: "runs" })} />
            )}
            {tab === "runs" && <RunList filter={{ script_id: current.id }} />}
            {tab === "schedules" && <SchedulesPane key={current.id} script={current} />}
            {tab === "edit" && (
              <Editor
                key={`${current.id}-${current.current_version}`}
                embedded
                scriptId={current.id}
                initial={{ ...current, note: "" }}
                onSaved={() => setPane({ kind: "script", id: current.id, tab: "run" })}
                onCancel={() => setPane({ kind: "script", id: current.id, tab: "run" })}
                onDeleted={() => setPane(null)}
              />
            )}
            {tab === "versions" && <Versions script={current} canRestore={writes} />}
          </section>
        )}
        {pane === null && list.data?.length === 0 && (
          <section className="card" style={{ margin: 0 }}>
            <p className="muted" style={{ margin: 0 }}>
              No scripts yet.{writes ? " Start with the example queries, or write your own." : " Admins add them."}
            </p>
          </section>
        )}
      </div>
    </section>
  );
}

function initialValue(p: ScriptParam): string | boolean {
  if (p.type === "bool") return Boolean(p.default);
  return p.default === null || p.default === undefined ? "" : String(p.default);
}

/** A query's parameters and period, then its result. An action shows its code (runs come with runners). */
function RunPane({ script, onStarted }: { script: Script; onStarted: () => void }) {
  const [values, setValues] = useState<Record<string, string | boolean>>(() => Object.fromEntries(script.parameters.map((p) => [p.name, initialValue(p)])));
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const body = { values: Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")), date_from: from || null, date_to: to || null, limit: 500 };
  const run = useMutation({ mutationFn: () => api.scripts.run(script.id, body) });
  const csv = useMutation({ mutationFn: () => api.scripts.downloadCsv(script.id, body, `${script.name.replace(/[^\w-]+/g, "-").toLowerCase()}.csv`) });
  const usesPeriod = /(?<!:):(start|end)\b/.test(script.body);

  if (script.kind === "action") {
    return (
      <div className="stack" style={{ gap: 10 }}>
        <details>
          <summary className="small">The script (version {script.current_version})</summary>
          <pre className="log-details code" style={{ margin: "6px 0 0", maxHeight: 420 }}>
            {script.body}
          </pre>
        </details>
        {script.can_run ? (
          <RunActionForm
            script={script}
            onStarted={onStarted}
            params={(vals, set) => script.parameters.map((p) => <ParamField key={p.name} param={p} value={vals[p.name]} onChange={(v) => set({ ...vals, [p.name]: v })} />)}
          />
        ) : (
          <p className="muted small" style={{ margin: 0 }}>
            Only admins can run this one.
          </p>
        )}
      </div>
    );
  }
  return (
    <form
      className="stack"
      style={{ gap: 10 }}
      onSubmit={(e) => {
        e.preventDefault();
        run.mutate();
      }}
    >
      {(script.parameters.length > 0 || usesPeriod) && (
        <div className="row-gap" style={{ alignItems: "flex-end" }}>
          {script.parameters.map((p) => (
            <ParamField key={p.name} param={p} value={values[p.name]} onChange={(v) => setValues({ ...values, [p.name]: v })} />
          ))}
          {usesPeriod && (
            <>
              <label className="field">
                From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <label className="field">
                To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </label>
            </>
          )}
        </div>
      )}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={!script.can_run || run.isPending}>
          {run.isPending ? "Running…" : "Run"}
        </button>
        <button className="btn2" type="button" disabled={!script.can_run || !run.data || csv.isPending} onClick={() => csv.mutate()}>
          Download CSV
        </button>
        {!script.can_run && <span className="muted small">Only admins can run this one.</span>}
      </div>
      {(run.error ?? csv.error) && (
        <p className="error" role="alert">
          {(run.error ?? csv.error)!.message}
        </p>
      )}
      {run.data && <section className="analysis-results" aria-label="Query results"><header><h3>Results</h3><span className="muted small" role="status">{run.data.rows.length} displayed row(s)</span></header><ResultTable result={run.data} /></section>}
      {run.data?.truncated && <p className="muted small">Showing the first {run.data.rows.length} rows.</p>}
    </form>
  );
}

/** Write or change a script. A changed body or parameters saves a new version. */
function Editor({
  initial,
  scriptId,
  embedded = false,
  onSaved,
  onCancel,
  onDeleted,
}: {
  initial: ScriptIn;
  scriptId?: number;
  embedded?: boolean;
  onSaved: (s: Script) => void;
  onCancel: () => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const [s, setS] = useState<ScriptIn>(initial);
  const params = s.parameters ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["scripts"] });
  const save = useMutation({ mutationFn: () => (scriptId ? api.scripts.update(scriptId, s) : api.scripts.create(s)), onSuccess: onSaved, onSettled: refresh });
  const remove = useMutation({ mutationFn: () => api.scripts.remove(scriptId!), onSuccess: onDeleted, onSettled: refresh });
  const setParam = (i: number, over: Partial<ScriptParam>) => setS({ ...s, parameters: params.map((p, j) => (j === i ? { ...p, ...over } : p)) });
  const languages = s.kind === "query" ? ["sql"] : ["powershell", "bash", "python", "eos"];

  return (
    <form
      className={`stack settings-form script-editor${embedded ? "" : " card"}`}
      style={{ margin: 0 }}
      aria-label={scriptId ? "Edit script" : "New script"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {!embedded && <div className="lbl">New script</div>}
      <label className="field">
        Name
        <input required maxLength={200} value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} />
      </label>
      <label className="field">
        Description
        <input value={s.description ?? ""} onChange={(e) => setS({ ...s, description: e.target.value })} />
      </label>
      <div className="row-gap">
        <label className="field">
          Kind
          <select
            value={s.kind}
            disabled={Boolean(scriptId)}
            onChange={(e) => {
              const kind = e.target.value as ScriptIn["kind"];
              setS({ ...s, kind, language: kind === "query" ? "sql" : "powershell" });
            }}
          >
            <option value="query">Query (read-only SQL)</option>
            <option value="action">Action (runs on devices)</option>
          </select>
        </label>
        <label className="field">
          Language
          <select value={s.language} onChange={(e) => setS({ ...s, language: e.target.value as ScriptIn["language"] })}>
            {languages.map((l) => (
              <option key={l} value={l}>
                {LANGUAGE_LABEL[l]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        {s.kind === "query" ? "SQL" : "Script"}
        <textarea className="code" rows={12} spellCheck={false} required value={s.body} onChange={(e) => setS({ ...s, body: e.target.value })} />
        <span className="muted small">
          {s.kind === "query"
            ? "One SELECT, read-only, on the restricted rules login. Use :name for a parameter, and :start / :end for the chosen period. Return device_id or user_id to make it a dynamic group."
            : "Parameters reach the script by name (PowerShell: $name; bash and Python: environment variables HD_NAME)."}
        </span>
      </label>
      <fieldset className="filter-box">
        <legend>Parameters</legend>
        <div className="stack" style={{ gap: 8 }}>
          {params.map((p, i) => (
            <div key={i} className="row-gap" role="group" aria-label={`Parameter ${i + 1}`}>
              <input aria-label="Name" placeholder="name" style={{ width: 130 }} value={p.name} onChange={(e) => setParam(i, { name: e.target.value })} />
              <input aria-label="Label" placeholder="Label" style={{ width: 180 }} value={p.label ?? ""} onChange={(e) => setParam(i, { label: e.target.value })} />
              <select aria-label="Type" style={{ width: 110 }} value={p.type ?? "text"} onChange={(e) => setParam(i, { type: e.target.value as ScriptParam["type"] })}>
                {["text", "int", "bool", "date", "choice"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <input aria-label="Default" placeholder="default" style={{ width: 110 }} value={p.default === null || p.default === undefined ? "" : String(p.default)} onChange={(e) => setParam(i, { default: e.target.value || null })} />
              {p.type === "choice" && (
                <input aria-label="Choices" placeholder="a, b, c" style={{ width: 160 }} value={(p.choices ?? []).join(", ")} onChange={(e) => setParam(i, { choices: e.target.value.split(",").map((c) => c.trim()) })} />
              )}
              <label className="inline small">
                <input type="checkbox" checked={Boolean(p.required)} onChange={(e) => setParam(i, { required: e.target.checked })} /> required
              </label>
              <button className="linkish small" type="button" onClick={() => setS({ ...s, parameters: params.filter((_, j) => j !== i) })}>
                Remove
              </button>
            </div>
          ))}
          <div>
            <button className="btn2" type="button" onClick={() => setS({ ...s, parameters: [...params, { name: "", label: "", type: "text", required: false, default: null, choices: [] }] })}>
              Add a parameter
            </button>
          </div>
        </div>
      </fieldset>
      <label className="inline">
        <input type="checkbox" checked={Boolean(s.agents_can_run)} onChange={(e) => setS({ ...s, agents_can_run: e.target.checked })} /> Agents can run it
      </label>
      {s.kind === "action" && (
        <label className="inline">
          <input type="checkbox" checked={Boolean(s.high_risk)} onChange={(e) => setS({ ...s, high_risk: e.target.checked })} /> High risk: an admin approves each run
        </label>
      )}
      <label className="inline">
        <input type="checkbox" checked={s.is_active ?? true} onChange={(e) => setS({ ...s, is_active: e.target.checked })} /> In use
      </label>
      <label className="field">
        What changed <span className="muted small">(kept with the version)</span>
        <input maxLength={2000} value={s.note ?? ""} onChange={(e) => setS({ ...s, note: e.target.value })} />
      </label>
      {(save.error ?? remove.error) && (
        <p className="error" role="alert">
          {(save.error ?? remove.error)!.message}
        </p>
      )}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {scriptId ? "Save" : "Create script"}
        </button>
        <button className="btn2" type="button" onClick={onCancel}>
          Cancel
        </button>
        <span className="spacer" />
        {scriptId && (
          <button
            className="linkish small"
            type="button"
            onClick={() => {
              if (window.confirm(`Delete "${initial.name}" and all its versions?`)) remove.mutate();
            }}
          >
            Delete script
          </button>
        )}
      </div>
    </form>
  );
}

/** Every saved version, newest first; open one to see its code and what changed. */
function Versions({ script, canRestore }: { script: Script; canRestore: boolean }) {
  const queryClient = useQueryClient();
  const versions = useQuery({ queryKey: ["script-versions", script.id, script.current_version], queryFn: () => api.scripts.versions(script.id) });
  const [open, setOpen] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["script-version", script.id, open], queryFn: () => api.scripts.version(script.id, open!), enabled: open !== null });
  const restore = useMutation({
    mutationFn: (n: number) => api.scripts.restore(script.id, n),
    onSuccess: () => setOpen(null),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["scripts"] }),
  });
  return (
    <div className="stack" style={{ gap: 8 }}>
      <table className="grid">
        <thead>
          <tr>
            <th>Version</th>
            <th>By</th>
            <th>When</th>
            <th>What changed</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {versions.data?.map((v) => (
            <tr key={v.number}>
              <td>
                {v.number}
                {v.is_current && <span className="chip g"> current</span>}
              </td>
              <td>{v.author?.display_name ?? "–"}</td>
              <td>{new Date(v.created_at).toLocaleString()}</td>
              <td>{v.note || "–"}</td>
              <td>
                <button className="linkish small" type="button" onClick={() => setOpen(open === v.number ? null : v.number)}>
                  {open === v.number ? "Hide" : "Show"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail.data && open !== null && (
        <div className="stack" style={{ gap: 6 }} aria-label={`Version ${open}`} role="region">
          <div className="lbl" style={{ margin: 0 }}>
            {detail.data.diff ? `Changes from the version before` : "First version"}
          </div>
          <pre className="log-details code diff" style={{ margin: 0 }}>
            {(detail.data.diff || detail.data.body).split("\n").map((line, i) => (
              <span key={i} className={detail.data!.diff ? (line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-del" : undefined) : undefined}>
                {line}
                {"\n"}
              </span>
            ))}
          </pre>
          {canRestore && !detail.data.is_current && (
            <div>
              <button className="btn2" type="button" disabled={restore.isPending} onClick={() => restore.mutate(open)}>
                Make version {open} current again
              </button>
            </div>
          )}
          {restore.error && <p className="error">{restore.error.message}</p>}
        </div>
      )}
    </div>
  );
}
