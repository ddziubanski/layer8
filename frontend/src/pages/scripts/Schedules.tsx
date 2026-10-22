import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Script, type ScriptParam, type ScriptSchedule, type ScriptScheduleIn } from "../../api/client";
import { describeTimetable, Timetable, type TimetableValue, todayIso } from "../../components/Timetable";
import { Recipients } from "../reports/EmailPanel";
import { ParamField } from "./ParamField";
import { RunStatusChip, TargetFields, type Targets } from "./Runs";

function startValues(params: ScriptParam[], given?: Record<string, unknown>): Record<string, string | boolean> {
  return Object.fromEntries(
    params.map((p) => {
      const v = given && p.name in given ? given[p.name] : p.default;
      return [p.name, p.type === "bool" ? Boolean(v) : v === null || v === undefined ? "" : String(v)];
    }),
  );
}

function where(s: ScriptSchedule): string {
  if (s.script_kind === "query") return `to ${s.recipients.join(", ")}`;
  if (s.on_runner) return "on the runner";
  const parts = [];
  if (s.devices.length) parts.push(s.devices.map((d) => d.name).join(", "));
  if (s.group_name) parts.push(`the devices in "${s.group_name}" on the day`);
  return `on ${parts.join(" and ") || "no devices"}`;
}

function bodyOf(s: ScriptSchedule, patch: Partial<ScriptScheduleIn> = {}): ScriptScheduleIn {
  return {
    name: s.name, script_id: s.script_id, values: s.values, device_ids: s.device_ids, group_script_id: s.group_script_id, on_runner: s.on_runner,
    period: s.period, recipients: s.recipients, message: s.message, frequency: s.frequency as ScriptScheduleIn["frequency"], weekdays: s.weekdays,
    day_of_month: s.day_of_month, time_of_day: s.time_of_day, start_date: s.start_date, is_active: s.is_active, ...patch,
  };
}

/** A script's schedules: when it runs by itself, on what (or to whom a query's result goes), and how the last run went. */
export function SchedulesPane({ script }: { script: Script }) {
  const queryClient = useQueryClient();
  const key = ["script-schedules", script.id];
  const list = useQuery({ queryKey: key, queryFn: () => api.scriptSchedules.list({ script_id: script.id }) });
  const [editing, setEditing] = useState<ScriptSchedule | "new" | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const runNow = useMutation({ mutationFn: api.scriptSchedules.runNow, onSettled: () => (refresh(), queryClient.invalidateQueries({ queryKey: ["runs"] })) });
  const toggle = useMutation({ mutationFn: (s: ScriptSchedule) => api.scriptSchedules.update(s.id, bodyOf(s, { is_active: !s.is_active })), onSettled: refresh });
  const remove = useMutation({ mutationFn: api.scriptSchedules.remove, onSettled: refresh });
  const error = runNow.error ?? toggle.error ?? remove.error;
  const schedules = list.data ?? [];

  if (editing !== null) {
    return <ScheduleForm script={script} initial={editing === "new" ? undefined : editing} onDone={() => (setEditing(null), refresh())} />;
  }
  return (
    <div className="stack" style={{ gap: 10 }}>
      <p className="muted small" style={{ margin: 0 }}>
        {script.kind === "query"
          ? "Run this query on a timetable and email the result as CSV."
          : "Run this action on a timetable, as you: on devices, a device group (worked out at each run) or the runner."}
        {script.high_risk && " It's high risk, so each scheduled run still waits for another admin's approval."}
      </p>
      {error && (
        <p className="error" role="alert" style={{ margin: 0 }}>
          {error.message}
        </p>
      )}
      {schedules.length === 0 && list.isSuccess && <p className="muted" style={{ margin: 0 }}>No schedules for this script yet.</p>}
      {schedules.map((s) => (
        <div key={s.id} className="schedule-row" aria-label={`Schedule ${s.name}`}>
          <div className="spacer stack" style={{ gap: 2 }}>
            <div>
              <strong>{s.name}</strong>
              {!s.is_active && <span className="chip n"> Paused</span>}
            </div>
            <div className="small">
              {describeTimetable(s)} · {where(s)}
            </div>
            <div className="muted small">
              {s.next_run_at ? `Next: ${new Date(s.next_run_at).toLocaleString()}` : "Not scheduled to run again"}
              {s.last_run_at && ` · Last: ${new Date(s.last_run_at).toLocaleString()}`}
              {s.owner && ` · runs as ${s.owner.display_name}`}
            </div>
            {s.last_error ? <div className="error small">Last run didn't happen: {s.last_error}</div> : s.last_result && <div className="small">Last run: {s.last_result}</div>}
            {s.recent_runs.length > 0 && (
              <div className="row-gap small" aria-label="Recent runs">
                {s.recent_runs.map((r) => (
                  <span key={r.id}>
                    {r.target ?? "runner"} <RunStatusChip status={r.status} notBefore={r.not_before} />
                  </span>
                ))}
              </div>
            )}
          </div>
          {s.can_edit && (
            <div className="row-gap">
              <button className="btn2" type="button" disabled={runNow.isPending} onClick={() => runNow.mutate(s.id)}>
                Run now
              </button>
              <button className="linkish small" type="button" onClick={() => setEditing(s)}>
                Edit
              </button>
              <button className="linkish small" type="button" onClick={() => toggle.mutate(s)}>
                {s.is_active ? "Pause" : "Resume"}
              </button>
              <button
                className="linkish small"
                type="button"
                onClick={() => {
                  if (window.confirm(`Delete the schedule "${s.name}"? Runs it already queued still run.`)) remove.mutate(s.id);
                }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      ))}
      {script.can_run && script.is_active && (
        <div>
          <button className="btn2" type="button" onClick={() => setEditing("new")}>
            New schedule…
          </button>
        </div>
      )}
    </div>
  );
}

function ScheduleForm({ script, initial, onDone }: { script: Script; initial?: ScriptSchedule; onDone: () => void }) {
  const periods = useQuery({ queryKey: ["report-periods"], queryFn: api.reports.periods, staleTime: Infinity, enabled: script.kind === "query" });
  const [name, setName] = useState(initial?.name ?? script.name);
  const [values, setValues] = useState(() => startValues(script.parameters, initial?.values));
  const [targets, setTargets] = useState<Targets>(() => ({
    target: initial?.on_runner ? "runner" : initial?.group_script_id ? "group" : "devices",
    devices: initial?.devices ?? [],
    groupId: initial?.group_script_id ?? null,
  }));
  const [period, setPeriod] = useState(initial?.period ?? "");
  const [recipients, setRecipients] = useState<string[]>(initial?.recipients ?? []);
  const [message, setMessage] = useState(initial?.message ?? "");
  const [time, setTime] = useState<TimetableValue>(() => ({
    frequency: (initial?.frequency as TimetableValue["frequency"]) ?? "weekly",
    weekdays: initial?.weekdays ?? [0],
    day_of_month: initial?.day_of_month ?? 1,
    time_of_day: initial?.time_of_day ?? "02:00",
    start_date: initial?.start_date ?? todayIso(),
  }));
  const usesPeriod = /(?<!:):(start|end)\b/.test(script.body);
  const isQuery = script.kind === "query";
  const body: ScriptScheduleIn = {
    name, script_id: script.id, values: Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")),
    device_ids: !isQuery && targets.target === "devices" ? targets.devices.map((d) => d.id) : [],
    group_script_id: !isQuery && targets.target === "group" ? targets.groupId : null,
    on_runner: !isQuery && targets.target === "runner",
    period: isQuery ? period : "", recipients: isQuery ? recipients : [], message: isQuery ? message : "",
    ...time, day_of_month: time.frequency === "monthly" ? time.day_of_month : null, is_active: initial?.is_active ?? true,
  };
  const save = useMutation({ mutationFn: () => (initial ? api.scriptSchedules.update(initial.id, body) : api.scriptSchedules.create(body)), onSuccess: onDone });
  const ready = isQuery ? recipients.length > 0 : targets.target === "runner" || (targets.target === "devices" && targets.devices.length > 0) || (targets.target === "group" && targets.groupId !== null);

  return (
    <form
      className="stack"
      style={{ gap: 10 }}
      aria-label={initial ? "Change the schedule" : "New schedule"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="field">
        Schedule name
        <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {script.parameters.length > 0 && (
        <div className="row-gap" style={{ alignItems: "flex-end" }}>
          {script.parameters.map((p) => (
            <ParamField key={p.name} param={p} value={values[p.name]} onChange={(v) => setValues({ ...values, [p.name]: v })} />
          ))}
        </div>
      )}
      {isQuery ? (
        <>
          {usesPeriod && (
            <label className="field">
              Period (:start and :end)
              <select value={period} onChange={(e) => setPeriod(e.target.value)}>
                <option value="">None</option>
                {Object.entries(periods.data ?? {}).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Recipients value={recipients} onChange={setRecipients} />
          <label className="field">
            Message <span className="muted small">(optional)</span>
            <textarea rows={2} maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
        </>
      ) : (
        <TargetFields value={targets} onChange={setTargets} groupNote="The group's devices are worked out at each run, so a device that joins it later is included." />
      )}
      <Timetable value={time} onChange={setTime} />
      {save.error && (
        <p className="error" role="alert" style={{ margin: 0 }}>
          {save.error.message}
        </p>
      )}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={!ready || save.isPending}>
          Save schedule
        </button>
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}
