import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ReportSchedule, type ReportScheduleIn, type ReportSpec } from "../../api/client";
import { describeTimetable, Timetable, todayIso } from "../../components/Timetable";
import { UserPicker } from "../../components/UserPicker";

type Format = "pdf" | "csv" | "both";

/** Pick people from the directory, or type any address; shown as removable chips. */
export function Recipients({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [typed, setTyped] = useState("");
  const add = (addr: string) => {
    const a = addr.trim();
    if (a && !value.some((v) => v.toLowerCase() === a.toLowerCase())) onChange([...value, a]);
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      <UserPicker label="Send to" value={null} onChange={(u) => u && add(u.email)} placeholder="Type a name to find someone" />
      <label className="field">
        <span className="muted small">or type an email address and press Enter</span>
        <input
          type="email"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(typed);
              setTyped("");
            }
          }}
          onBlur={() => {
            add(typed);
            setTyped("");
          }}
          aria-label="Email address"
        />
      </label>
      {value.length > 0 && (
        <ul className="chips" aria-label="Recipients">
          {value.map((r) => (
            <li key={r} className="chip n">
              {r}{" "}
              <button type="button" className="chip-x" aria-label={`Remove ${r}`} onClick={() => onChange(value.filter((v) => v !== r))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Target = { title: string; description: string; spec: ReportSpec; presetKey?: string; savedReportId?: number; unsaved: boolean };

/** Email the report now, or on a schedule. */
export function EmailPanel({ target, onClose, onScheduled }: { target: Target; onClose: () => void; onScheduled: () => void }) {
  const queryClient = useQueryClient();
  const periods = useQuery({ queryKey: ["report-periods"], queryFn: api.reports.periods, staleTime: Infinity });
  const [mode, setMode] = useState<"now" | "schedule">("now");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [format, setFormat] = useState<Format>("pdf");
  const [message, setMessage] = useState("");
  const [sched, setSched] = useState({
    name: target.title,
    period: "previous_week",
    frequency: "weekly" as ReportScheduleIn["frequency"],
    weekdays: [0],
    day_of_month: 1,
    time_of_day: "07:00",
    start_date: todayIso(),
  });

  const send = useMutation({
    mutationFn: () => api.reports.email({ spec: target.spec, title: target.title, description: target.description, recipients, format, message }),
  });
  const schedule = useMutation({
    mutationFn: () =>
      api.reports.createSchedule({
        ...sched,
        day_of_month: sched.frequency === "monthly" ? sched.day_of_month : null,
        preset_key: target.savedReportId ? null : (target.presetKey ?? null),
        saved_report_id: target.savedReportId ?? null,
        recipients,
        format,
        message,
        is_active: true,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
      onScheduled();
    },
  });
  const error = send.error ?? schedule.error;

  return (
    <form
      className="card stack email-panel"
      style={{ margin: 0 }}
      aria-label="Email or schedule"
      onSubmit={(e) => {
        e.preventDefault();
        if (mode === "now") send.mutate();
        else schedule.mutate();
      }}
    >
      <div className="row-gap">
        <div className="views" role="group" aria-label="When" style={{ margin: 0 }}>
          <button type="button" aria-pressed={mode === "now"} onClick={() => setMode("now")}>
            Send now
          </button>
          <button type="button" aria-pressed={mode === "schedule"} onClick={() => setMode("schedule")}>
            Schedule
          </button>
        </div>
        <span className="spacer" />
        <button type="button" className="linkish small" onClick={onClose}>
          Close
        </button>
      </div>
      <Recipients value={recipients} onChange={setRecipients} />
      <div className="row-gap">
        <span className="small">Attach as</span>
        {(["pdf", "csv", "both"] as Format[]).map((f) => (
          <label key={f} className="inline small">
            <input type="radio" name="format" checked={format === f} onChange={() => setFormat(f)} /> {f === "both" ? "PDF and CSV" : f.toUpperCase()}
          </label>
        ))}
      </div>
      <label className="field">
        Message <span className="muted small">(optional)</span>
        <textarea rows={2} maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)} />
      </label>

      {mode === "schedule" && (
        <div className="stack" style={{ gap: 10 }}>
          {target.unsaved && <p className="error small">This report has unsaved changes. Save it first; a schedule sends the saved version.</p>}
          <label className="field">
            Schedule name
            <input required maxLength={200} value={sched.name} onChange={(e) => setSched({ ...sched, name: e.target.value })} />
          </label>
          <div className="row-gap">
            <label className="field spacer">
              Report covers
              <select value={sched.period} onChange={(e) => setSched({ ...sched, period: e.target.value })}>
                {Object.entries(periods.data ?? { previous_week: "Previous week (Mon–Sun)" }).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <Timetable value={sched} onChange={(t) => setSched({ ...sched, ...t, day_of_month: t.day_of_month ?? 1 })} />
        </div>
      )}

      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
      {send.data && (
        <p className="small" role="status">
          Sent to {send.data.queued} {send.data.queued === 1 ? "person" : "people"}. It goes out on the next mailbox check.
        </p>
      )}
      <div className="row-gap">
        <span className="spacer" />
        <button className="btn" type="submit" disabled={recipients.length === 0 || send.isPending || schedule.isPending || (mode === "schedule" && target.unsaved)}>
          {mode === "now" ? "Send report" : "Save schedule"}
        </button>
      </div>
    </form>
  );
}

/** Your scheduled reports (admins: everyone's). */
export function SchedulesView() {
  const queryClient = useQueryClient();
  const schedules = useQuery({ queryKey: ["report-schedules"], queryFn: api.reports.schedules });
  const periods = useQuery({ queryKey: ["report-periods"], queryFn: api.reports.periods, staleTime: Infinity });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
  const sendNow = useMutation({ mutationFn: api.reports.sendSchedule, onSettled: refresh });
  const toggle = useMutation({
    mutationFn: (s: ReportSchedule) =>
      api.reports.updateSchedule(s.id, {
        name: s.name,
        preset_key: s.preset_key,
        saved_report_id: s.saved_report_id,
        period: s.period,
        recipients: s.recipients,
        format: s.format as Format,
        message: s.message,
        frequency: s.frequency as ReportScheduleIn["frequency"],
        weekdays: s.weekdays,
        day_of_month: s.day_of_month,
        time_of_day: s.time_of_day,
        start_date: s.start_date,
        is_active: !s.is_active,
      }),
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: api.reports.deleteSchedule, onSettled: refresh });
  const error = sendNow.error ?? toggle.error ?? remove.error;
  const list = schedules.data ?? [];

  return (
    <section className="card stack" style={{ margin: 0 }} aria-label="Scheduled reports">
      <div>
        <h2 style={{ margin: 0 }}>Scheduled reports</h2>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Reports emailed on a timetable. Open a report and choose “Email or schedule…” to add one.
        </p>
      </div>
      {error && <p className="error">{error.message}</p>}
      {list.length === 0 && schedules.isSuccess && <p className="muted">No scheduled reports yet.</p>}
      {list.map((s) => (
        <div key={s.id} className="schedule-row">
          <div className="spacer">
            <div>
              <strong>{s.name}</strong> <span className="muted small">· {s.report_title}</span>
              {!s.is_active && <span className="chip n"> Paused</span>}
            </div>
            <div className="small">
              {describeTimetable(s)} · covers {(periods.data?.[s.period] ?? s.period).toLowerCase()} · {s.format === "both" ? "PDF and CSV" : s.format.toUpperCase()} to {s.recipients.join(", ")}
            </div>
            <div className="muted small">
              {s.next_run_at ? `Next: ${new Date(s.next_run_at).toLocaleString()}` : "Not scheduled to run again"}
              {s.last_run_at && ` · Last: ${new Date(s.last_run_at).toLocaleString()}`}
              {s.owner && ` · by ${s.owner.display_name}`}
            </div>
            {s.last_error && <div className="error small">Last run failed: {s.last_error}</div>}
          </div>
          {s.can_edit && (
            <div className="row-gap">
              <button className="btn2" type="button" disabled={sendNow.isPending} onClick={() => sendNow.mutate(s.id)}>
                Send now
              </button>
              <button className="linkish small" type="button" onClick={() => toggle.mutate(s)}>
                {s.is_active ? "Pause" : "Resume"}
              </button>
              <button
                className="linkish small"
                type="button"
                onClick={() => {
                  if (window.confirm(`Delete the schedule "${s.name}"?`)) remove.mutate(s.id);
                }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      ))}
      {sendNow.isSuccess && <p className="small" role="status">Sent. It goes out on the next mailbox check.</p>}
    </section>
  );
}
