// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Notice, type NoticeIn, type Me } from "../../api/client";
import { SEVERITY } from "../../components/Notices";
import { when } from "../../format";
import { can } from "../../permissions";

const AUDIENCE: Record<string, string> = { everyone: "Everyone", staff: "Technicians and admins", requesters: "Requesters (portal)" };
const STATE: Record<string, [string, string]> = { current: ["g", "Showing now"], upcoming: ["n", "Scheduled"], ended: ["n", "Ended"] };

// <input type="datetime-local"> works in local time without a zone.
const toLocalInput = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fromLocalInput = (v: string) => (v ? new Date(v).toISOString() : null);

type Draft = { title: string; message: string; severity: NoticeIn["severity"]; audience: NoticeIn["audience"]; starts_at: string; ends_at: string };
const EMPTY: Draft = { title: "", message: "", severity: "warning", audience: "everyone", starts_at: "", ends_at: "" };

/** Settings > Notices: tell people about outages and known problems. */
export function NoticesAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const notices = useQuery({ queryKey: ["notices", "all"], queryFn: api.notices.all, enabled: can(me, "notices_manage") });
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["notices"] });
  const save = useMutation({
    mutationFn: () => {
      const body: NoticeIn = { ...draft, starts_at: fromLocalInput(draft.starts_at), ends_at: fromLocalInput(draft.ends_at) };
      return editing === "new" ? api.notices.post(body) : api.notices.update(editing as number, body);
    },
    onSuccess: () => setEditing(null),
    onSettled: refresh,
  });
  const end = useMutation({ mutationFn: api.notices.end, onSettled: refresh });
  const remove = useMutation({ mutationFn: api.notices.remove, onSettled: refresh });

  if (!can(me, "notices_manage")) return <p>Notices are managed by admins.</p>;
  const set = (over: Partial<Draft>) => setDraft((d) => ({ ...d, ...over }));
  const edit = (n: Notice) => {
    setEditing(n.id);
    setDraft({ title: n.title, message: n.message, severity: n.severity as Draft["severity"], audience: n.audience as Draft["audience"], starts_at: toLocalInput(n.starts_at), ends_at: toLocalInput(n.ends_at) });
  };
  const error = save.error ?? end.error ?? remove.error;

  return (
    <>
      <div className="page-head">
        <h1>Notices</h1>
        <span className="spacer" />
        {editing === null && (
          <button
            className="btn"
            type="button"
            onClick={() => {
              setEditing("new");
              setDraft(EMPTY);
            }}
          >
            Post a notice
          </button>
        )}
      </div>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          Tell people about an outage or a known problem. It's shown right after they sign in and across the top of the console and portal, so
          they don't log the same problem again.
        </p>
        {editing !== null && (
          <form
            className="card stack settings-form"
            style={{ margin: 0 }}
            aria-label={editing === "new" ? "New notice" : "Edit notice"}
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            <label className="field">
              Title
              <input required autoFocus maxLength={200} placeholder="e.g. Email is down" value={draft.title} onChange={(e) => set({ title: e.target.value })} />
            </label>
            <label className="field">
              Details <span className="muted small">(optional)</span>
              <textarea rows={3} maxLength={4000} placeholder="What's affected, what people can do meanwhile, when you expect it fixed." value={draft.message} onChange={(e) => set({ message: e.target.value })} />
            </label>
            <div className="row-gap">
              <label className="field spacer">
                Severity
                <select value={draft.severity} onChange={(e) => set({ severity: e.target.value as Draft["severity"] })}>
                  <option value="outage">Outage</option>
                  <option value="warning">Warning</option>
                  <option value="info">Information</option>
                </select>
              </label>
              <label className="field spacer">
                Show to
                <select value={draft.audience} onChange={(e) => set({ audience: e.target.value as Draft["audience"] })}>
                  {Object.entries(AUDIENCE).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="row-gap">
              <label className="field spacer">
                From <span className="muted small">(blank: now)</span>
                <input type="datetime-local" value={draft.starts_at} onChange={(e) => set({ starts_at: e.target.value })} />
              </label>
              <label className="field spacer">
                Until <span className="muted small">(blank: until ended)</span>
                <input type="datetime-local" value={draft.ends_at} onChange={(e) => set({ ends_at: e.target.value })} />
              </label>
            </div>
            <div className="row-gap">
              <button className="btn2" type="button" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button className="btn" type="submit" disabled={save.isPending}>
                {editing === "new" ? "Post notice" : "Save"}
              </button>
            </div>
          </form>
        )}
        {error && (
          <p className="error" role="alert">
            {error.message}
          </p>
        )}
        <section className="card stack" style={{ margin: 0 }} aria-label="All notices">
          {(notices.data ?? []).length === 0 && notices.isSuccess && <p className="muted" style={{ margin: 0 }}>No notices yet.</p>}
          {(notices.data ?? []).map((n) => {
            const s = SEVERITY[n.severity] ?? SEVERITY.info;
            const [chip, label] = STATE[n.state] ?? STATE.ended;
            return (
              <div key={n.id} className="schedule-row">
                <div className="spacer">
                  <div>
                    <span className={`chip ${s.chip}`}>
                      <span aria-hidden="true">{s.icon}</span> {s.label}
                    </span>{" "}
                    <strong>{n.title}</strong> <span className={`chip ${chip}`}>{label}</span>
                  </div>
                  {n.message && <div className="small">{n.message}</div>}
                  <div className="muted small">
                    {AUDIENCE[n.audience]} · from {when(n.starts_at)}
                    {n.ends_at ? ` until ${when(n.ends_at)}` : " until ended"}
                    {n.created_by && ` · by ${n.created_by.display_name}`}
                  </div>
                </div>
                <div className="row-gap">
                  {n.state !== "ended" && (
                    <button className="btn2" type="button" disabled={end.isPending} onClick={() => end.mutate(n.id)}>
                      End now
                    </button>
                  )}
                  <button className="linkish small" type="button" onClick={() => edit(n)}>
                    Edit
                  </button>
                  <button
                    className="linkish small"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Delete the notice "${n.title}"?`)) remove.mutate(n.id);
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </section>
      </div>
    </>
  );
}
