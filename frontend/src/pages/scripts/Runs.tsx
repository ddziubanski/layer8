// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Device, type Run, type RunRequest, type Script, type ScriptParam } from "../../api/client";
import { DevicePicker } from "../../components/DevicePicker";
import { when } from "../../format";

export const RUN_STATUS: Record<string, [string, string]> = {
  awaiting_approval: ["Waiting for approval", "a"],
  queued: ["Queued", "n"],
  running: ["Running", "a"],
  succeeded: ["Succeeded", "g"],
  failed: ["Failed", "r"],
  rejected: ["Not approved", "r"],
  cancelled: ["Cancelled", "n"],
};

const waiting = (r: { status: string; not_before?: string | null }) => r.status === "queued" && !!r.not_before && new Date(r.not_before) > new Date();

/** Waiting for a runner or a decision now (a run that waits for a date isn't, yet). */
export const busy = (r: { status: string; not_before?: string | null }) => ["awaiting_approval", "queued", "running"].includes(r.status) && !waiting(r);

export function RunStatusChip({ status, notBefore }: { status: string; notBefore?: string | null }) {
  if (waiting({ status, not_before: notBefore })) return <span className="chip n">Waits until {new Date(notBefore!).toLocaleDateString()}</span>;
  const [label, tone] = RUN_STATUS[status] ?? [status, "n"];
  return <span className={`chip ${tone}`}>{label}</span>;
}

export type Where = "devices" | "group" | "runner";
// A device to run on: all the form needs of it.
export type Target = Pick<Device, "id" | "name">;
export type Targets = { target: Where; devices: Target[]; groupId: number | null };

/** Where an action runs: picked devices, a device group (a query returning device_id), or the runner itself. */
export function TargetFields({ value, onChange, groupNote }: { value: Targets; onChange: (v: Targets) => void; groupNote?: string }) {
  const { target, devices, groupId } = value;
  const set = (patch: Partial<Targets>) => onChange({ ...value, ...patch });
  const scripts = useQuery({ queryKey: ["scripts"], queryFn: api.scripts.list, enabled: target === "group" });
  const groups = (scripts.data ?? []).filter((s) => s.kind === "query" && s.can_run);
  return (
    <fieldset className="filter-box">
      <legend>Run it on</legend>
      <div className="row-gap">
        {(
          [
            ["devices", "Devices"],
            ["group", "A device group (query)"],
            ["runner", "The runner itself"],
          ] as [Where, string][]
        ).map(([v, label]) => (
          <label key={v} className="inline small">
            <input type="radio" name="target" checked={target === v} onChange={() => set({ target: v })} /> {label}
          </label>
        ))}
      </div>
      {target === "devices" && (
        <div className="stack" style={{ gap: 6, marginTop: 8 }}>
          {devices.length > 0 && (
            <div className="row-gap">
              {devices.map((d) => (
                <span key={d.id} className="chip n">
                  {d.name}{" "}
                  <button className="linkish small" type="button" aria-label={`Remove ${d.name}`} onClick={() => set({ devices: devices.filter((x) => x.id !== d.id) })}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <DevicePicker label="Add a device" exclude={devices.map((d) => d.id)} onPick={(d) => set({ devices: [...devices, d] })} />
        </div>
      )}
      {target === "group" && (
        <>
          <label className="field" style={{ marginTop: 8 }}>
            Group
            <select value={groupId ?? ""} onChange={(e) => set({ groupId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Choose a query that returns device_id…</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
          {groupNote && <p className="muted small" style={{ margin: "6px 0 0" }}>{groupNote}</p>}
        </>
      )}
      {target === "runner" && <p className="muted small" style={{ margin: "8px 0 0" }}>For tasks that don't belong to one device, e.g. unlocking an AD account.</p>}
    </fieldset>
  );
}

/** Run an action: parameters, where (devices, a device group, or the runner itself), a preview, then go. */
export function RunActionForm({
  script,
  params,
  ticketId,
  initialDevices = [],
  onStarted,
}: {
  script: Script;
  params: (values: Record<string, string | boolean>, set: (v: Record<string, string | boolean>) => void) => React.ReactNode;
  ticketId?: number;
  initialDevices?: Target[];
  onStarted?: (runs: Run[]) => void;
}) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(script.parameters.map((p: ScriptParam) => [p.name, p.type === "bool" ? Boolean(p.default) : p.default == null ? "" : String(p.default)])),
  );
  const [target, setTarget] = useState<Where>("devices");
  const [devices, setDevices] = useState<Target[]>(initialDevices);
  const [groupId, setGroupId] = useState<number | null>(null);
  const body: RunRequest = {
    device_ids: target === "devices" ? devices.map((d) => d.id) : [],
    group_script_id: target === "group" ? groupId : null,
    on_runner: target === "runner",
    values: Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")),
    ticket_id: ticketId ?? null,
  };
  const preview = useMutation({ mutationFn: () => api.runs.preview(script.id, body) });
  const start = useMutation({
    mutationFn: () => api.runs.start(script.id, body),
    onSuccess: (runs) => {
      preview.reset();
      onStarted?.(runs);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["runs"] }),
  });
  const ready = target === "runner" || (target === "devices" && devices.length > 0) || (target === "group" && groupId !== null);
  const error = preview.error ?? start.error;

  return (
    <form
      className="stack"
      style={{ gap: 10 }}
      aria-label="Run this script"
      onSubmit={(e) => {
        e.preventDefault();
        preview.mutate();
      }}
    >
      {script.parameters.length > 0 && <div className="row-gap" style={{ alignItems: "flex-end" }}>{params(values, setValues)}</div>}
      <TargetFields
        value={{ target, devices, groupId }}
        onChange={(v) => {
          setTarget(v.target);
          setDevices(v.devices);
          setGroupId(v.groupId);
          preview.reset();
        }}
      />
      {!preview.data && (
        <div>
          <button className="btn2" type="submit" disabled={!ready || preview.isPending}>
            Check targets…
          </button>
        </div>
      )}
      {preview.data && (
        <div className="card note stack" style={{ margin: 0, gap: 6 }} aria-label="Before it runs" role="region">
          <strong>
            {preview.data.targets.length === 1 ? "1 run" : `${preview.data.targets.length} runs`}: {preview.data.targets.map((t) => t.target ?? t.name).join(", ") || "no devices"}
          </strong>
          {Object.keys(preview.data.values).length > 0 && (
            <span className="small">
              With{" "}
              {Object.entries(preview.data.values)
                .map(([k, v]) => `${k} = ${v === null ? "(empty)" : String(v)}`)
                .join(", ")}
            </span>
          )}
          {preview.data.needs_approval && <span className="small">High risk: each run waits for another admin's approval.</span>}
          {preview.data.runners.length === 0 ? (
            <span className="error small">No runner takes {script.language} yet (Settings &gt; Runners): runs will wait.</span>
          ) : (
            !preview.data.runner_online && <span className="small">No {preview.data.runners.join("/")} runner has checked in lately: runs will wait for it.</span>
          )}
          <div className="row-gap">
            <button className="btn" type="button" disabled={start.isPending || preview.data.targets.length === 0} onClick={() => start.mutate()}>
              {preview.data.needs_approval ? "Ask for approval" : "Run now"}
            </button>
            <button className="btn2" type="button" onClick={() => preview.reset()}>
              Change
            </button>
          </div>
        </div>
      )}
      {start.data && (
        <p className="small" role="status" style={{ margin: 0 }}>
          {start.data.length} run(s) {start.data[0]?.status === "awaiting_approval" ? "waiting for approval" : "queued"}.
        </p>
      )}
      {error && (
        <p className="error" role="alert" style={{ margin: 0 }}>
          {error.message}
        </p>
      )}
    </form>
  );
}

/** Runs, newest first: status, where, who; open one for its output (updates while it runs). */
export function RunList({ filter, showScript = false }: { filter: { script_id?: number; ticket_id?: number; status?: string }; showScript?: boolean }) {
  const runs = useQuery({
    queryKey: ["runs", filter],
    queryFn: () => api.runs.list({ ...filter, limit: 50 }),
    refetchInterval: (q) => (q.state.data?.some(busy) ? 3000 : false),
  });
  const [open, setOpen] = useState<number | null>(null);
  if (runs.error) return <p className="error">{runs.error.message}</p>;
  if (runs.data?.length === 0) return <p className="muted small" style={{ margin: 0 }}>No runs yet.</p>;
  return (
    <div className="stack" style={{ gap: 6 }}>
      {runs.data?.map((r) => (
        <div key={r.id} className="card" style={{ padding: 10 }}>
          <div className="row-gap">
            <RunStatusChip status={r.status} notBefore={r.not_before} />
            <span className="spacer">
              {showScript && <strong>{r.script_name} · </strong>}
              {r.target ?? "on the runner"}
              <span className="muted small">
                {" "}
                · #{r.id} · v{r.version} · {r.requested_by?.display_name ?? "?"}, {when(r.created_at)}
                {r.ticket_id && !filter.ticket_id && (
                  <>
                    {" "}
                    · <Link to={`/tickets/${r.ticket_id}`}>ticket #{r.ticket_id}</Link>
                  </>
                )}
              </span>
            </span>
            <RunActions run={r} />
            <button className="linkish small" type="button" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
              {open === r.id ? "Hide" : "Output"}
            </button>
          </div>
          {open === r.id && <RunOutput id={r.id} />}
        </div>
      ))}
    </div>
  );
}

function RunActions({ run }: { run: Run }) {
  const queryClient = useQueryClient();
  const done = { onSettled: () => queryClient.invalidateQueries({ queryKey: ["runs"] }) };
  const approve = useMutation({ mutationFn: () => api.runs.approve(run.id), ...done });
  const reject = useMutation({ mutationFn: () => api.runs.reject(run.id), ...done });
  const cancel = useMutation({ mutationFn: () => api.runs.cancel(run.id), ...done });
  const error = approve.error ?? reject.error ?? cancel.error;
  return (
    <>
      {run.can_decide && (
        <>
          <button className="btn" style={{ minHeight: 0, padding: "6px 10px" }} type="button" disabled={approve.isPending} onClick={() => approve.mutate()}>
            Approve
          </button>
          <button className="btn2" style={{ minHeight: 0, padding: "6px 10px" }} type="button" disabled={reject.isPending} onClick={() => reject.mutate()}>
            Reject
          </button>
        </>
      )}
      {run.can_cancel && (
        <button className="linkish small" type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
          Cancel
        </button>
      )}
      {error && <span className="error small">{error.message}</span>}
    </>
  );
}

function RunOutput({ id }: { id: number }) {
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.runs.get(id),
    refetchInterval: (q) => (["queued", "running", "awaiting_approval"].includes(q.state.data?.status ?? "") ? 2000 : false),
  });
  const r = run.data;
  if (!r) return null;
  return (
    <div className="stack" style={{ gap: 6, marginTop: 8 }}>
      <span className="muted small">
        {r.runner_name && `Runner ${r.runner_name}. `}
        {r.decided_by && `${r.status === "rejected" ? "Rejected" : r.status === "cancelled" ? "Cancelled" : "Approved"} by ${r.decided_by.display_name}. `}
        {r.exit_code !== null && `Exit code ${r.exit_code}. `}
        {Object.keys(r.values).length > 0 && `Values: ${Object.entries(r.values).map(([k, v]) => `${k}=${String(v)}`).join(", ")}.`}
      </span>
      {r.error && <span className="error small">{r.error}</span>}
      <pre className="log-details code" style={{ margin: 0 }} aria-label="Output">
        {r.output || (r.status === "running" ? "Waiting for output…" : "No output.")}
      </pre>
    </div>
  );
}
