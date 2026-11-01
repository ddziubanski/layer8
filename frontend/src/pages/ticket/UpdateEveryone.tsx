// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Ticket } from "../../api/client";
import type { components } from "../../api/schema";

type S = components["schemas"];

type Broadcast = Awaited<ReturnType<typeof api.broadcast.preview>>;

/** One message to everyone affected, previewed first: a ticket and its open child tickets,
 * or (with problemId) every open ticket linked to a problem. */
export function UpdateEveryone({ ticket, problemId, onDone }: { ticket?: Ticket; problemId?: number; onDone: () => void }) {
  const queryClient = useQueryClient();
  const forProblem = problemId !== undefined;
  const api_ = forProblem
    ? { preview: (b: S["BroadcastIn"]) => api.problems.broadcastPreview(problemId, b), send: (b: S["BroadcastIn"]) => api.problems.broadcastSend(problemId, b) }
    : { preview: (b: S["BroadcastIn"]) => api.broadcast.preview(ticket!.id, b), send: (b: S["BroadcastIn"]) => api.broadcast.send(ticket!.id, b) };
  const [body, setBody] = useState("");
  const [resolve, setResolve] = useState(false);
  const [resolution, setResolution] = useState("");
  const [closure, setClosure] = useState("");
  const [preview, setPreview] = useState<Broadcast | null>(null);
  const resolutions = useQuery({ queryKey: ["field-options", "resolution_type"], queryFn: () => api.fieldOptions("resolution_type"), enabled: resolve });
  const closures = useQuery({ queryKey: ["field-options", "closure_reason"], queryFn: () => api.fieldOptions("closure_reason"), enabled: resolve });
  const request = () => ({
    body, resolve,
    resolution_type_id: resolve && resolution ? Number(resolution) : null,
    closure_reason_id: resolve && closure ? Number(closure) : null,
  });
  const check = useMutation({ mutationFn: () => api_.preview({ ...request(), confirm: false }), onSuccess: setPreview });
  const send = useMutation({
    mutationFn: () => api_.send({ ...request(), fingerprint: preview!.fingerprint, confirm: true }),
    onSuccess: () => {
      if (forProblem) void queryClient.invalidateQueries({ queryKey: ["problem", problemId] });
      else void queryClient.invalidateQueries({ queryKey: ["ticket", ticket!.id] });
      void queryClient.invalidateQueries({ queryKey: ["tickets"] });
      onDone();
    },
  });
  // Any edit after a preview needs a fresh one.
  const edit = <T,>(fn: (v: T) => void) => (v: T) => { fn(v); setPreview(null); };
  const resolving = preview?.tickets.filter((r) => r.resolved).length ?? 0;

  return (
    <div className="card stack update-everyone" role="region" aria-label="Update everyone affected">
      <label className="field">
        Update for everyone affected
        <textarea rows={4} value={body} maxLength={20000} onChange={(e) => edit(setBody)(e.target.value)}
          placeholder="What happened and what to expect. Only this text is sent: no internal notes." />
      </label>
      <label className="inline"><input type="checkbox" checked={resolve} onChange={(e) => edit(setResolve)(e.target.checked)} />Resolve them all</label>
      {resolve && (
        <div className="row-gap">
          {(resolutions.data?.length ?? 0) > 0 && (
            <label className="field spacer">Resolution type
              <select value={resolution} onChange={(e) => edit(setResolution)(e.target.value)}>
                <option value="">Choose…</option>
                {resolutions.data!.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          )}
          {(closures.data?.length ?? 0) > 0 && (
            <label className="field spacer">Closure reason
              <select value={closure} onChange={(e) => edit(setClosure)(e.target.value)}>
                <option value="">Choose…</option>
                {closures.data!.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      {check.error && <p className="error">{check.error.message}</p>}
      {!preview ? (
        <div className="row-gap">
          <button className="btn" type="button" disabled={!body.trim() || check.isPending} onClick={() => check.mutate()}>Preview</button>
          <button className="btn2" type="button" onClick={onDone}>Cancel</button>
        </div>
      ) : (
        <>
          <table className="grid" aria-label="Who this update reaches">
            <thead><tr><th>Ticket</th><th>Requester</th><th>Email</th>{preview.resolve && <th>Resolved</th>}</tr></thead>
            <tbody>
              {preview.tickets.map((r) => (
                <tr key={r.ticket.id}>
                  <td>{r.ticket.label} {r.ticket.subject}</td>
                  <td>{r.requester}</td>
                  <td>{r.emailed ? "Yes" : <span className="muted">No: {r.why_no_email}</span>}</td>
                  {preview.resolve && <td>{r.resolved ? "Yes" : <span className="muted">{r.note}</span>}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            Each ticket gets this as a public reply.{preview.skipped_closed > 0 && ` ${preview.skipped_closed} already resolved or closed ${forProblem ? "linked" : "child"} ticket(s) are left alone.`}
            {forProblem && preview.resolve && " When every linked ticket is resolved, the problem is marked resolved and its service notice ends."}
            {preview.resolve && " If resolved emails are on, requesters also get the usual resolved email."}
          </p>
          {send.error && <p className="error">{send.error.message}</p>}
          <div className="row-gap">
            <button className="btn" type="button" disabled={send.isPending} onClick={() => send.mutate()}>
              {`Send to ${preview.emails} ${preview.emails === 1 ? "person" : "people"}`}{preview.resolve ? ` and resolve ${resolving}` : ""}
            </button>
            <button className="btn2" type="button" disabled={send.isPending} onClick={() => setPreview(null)}>Change</button>
          </div>
        </>
      )}
    </div>
  );
}
