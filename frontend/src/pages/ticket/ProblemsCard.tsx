// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { ProblemStatus } from "../problems/status";

/** The problems this ticket belongs to, with what to do now (the workaround); link or start one here. */
export function ProblemsCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket", ticket.id, "problems"];
  const linked = useQuery({ queryKey: key, queryFn: () => api.problems.forTicket(ticket.id) });
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState("");
  const matches = useQuery({ queryKey: ["problems", "pick", q], queryFn: () => api.problems.list({ q }), enabled: searching });
  const done = (data: Awaited<ReturnType<typeof api.problems.forTicket>>) => {
    queryClient.setQueryData(key, data);
    void queryClient.invalidateQueries({ queryKey: ["problems"] });
    void queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "events"] });
    onChanged();
  };
  const link = useMutation({ mutationFn: (id: number) => api.problems.link(ticket.id, id), onSuccess: (data) => { setSearching(false); setQ(""); done(data); } });
  const unlink = useMutation({ mutationFn: (id: number) => api.problems.unlink(ticket.id, id), onSuccess: done });
  const problems = linked.data?.problems ?? [];
  const already = new Set(problems.map((p) => p.id));
  const error = link.error ?? unlink.error;

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Problems">
      <div className="lbl" style={{ margin: 0 }}>Problems</div>
      {linked.isPending && <p className="muted small" style={{ margin: 0 }}>Loading…</p>}
      {linked.error && <p className="error small" style={{ margin: 0 }}>{linked.error.message}</p>}
      {linked.data && problems.length === 0 && !linked.data.hidden && (
        <p className="muted small" style={{ margin: 0 }}>Not part of a problem. Link one when this ticket shares a root cause with others.</p>
      )}
      {problems.map((p) => (
        <div key={p.id} className="stack" style={{ gap: 4 }}>
          <div className="row-gap">
            <Link to={`/problems/${p.id}`} className="spacer">{p.label} {p.title}</Link>
            <ProblemStatus status={p.status} />
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            {p.ticket_count} linked ticket{p.ticket_count === 1 ? "" : "s"} · {p.owner?.display_name ?? "No owner"}
          </p>
          {p.workaround && <p className="small" style={{ margin: 0, whiteSpace: "pre-wrap" }}><strong>Workaround:</strong> {p.workaround}</p>}
          {p.root_cause && <p className="small" style={{ margin: 0, whiteSpace: "pre-wrap" }}><strong>Root cause:</strong> {p.root_cause}</p>}
          <div>
            <button className="linkish small" type="button" disabled={unlink.isPending} onClick={() => unlink.mutate(p.id)}>Unlink from {p.label}</button>
          </div>
        </div>
      ))}
      {!!linked.data?.hidden && (
        <p className="muted small" style={{ margin: 0 }}>
          Also part of {linked.data.hidden} problem{linked.data.hidden === 1 ? "" : "s"} you can&apos;t open: {linked.data.hidden === 1 ? "it links" : "they link"} tickets you can&apos;t see.
        </p>
      )}
      {error && <p className="error small" role="alert" style={{ margin: 0 }}>{error.message}</p>}
      {searching ? (
        <div className="stack" style={{ gap: 6 }}>
          <label className="field">Find a problem
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Words from its title, cause or workaround" />
          </label>
          {matches.isPending && <p className="muted small" style={{ margin: 0 }}>Searching…</p>}
          {matches.data?.filter((p) => !already.has(p.id)).slice(0, 8).map((p) => (
            <div key={p.id} className="row-gap">
              <span className="spacer small">{p.label} {p.title}</span>
              <ProblemStatus status={p.status} />
              <button className="btn2" type="button" disabled={link.isPending} onClick={() => link.mutate(p.id)}>Link</button>
            </div>
          ))}
          {matches.data && matches.data.filter((p) => !already.has(p.id)).length === 0 && <p className="muted small" style={{ margin: 0 }}>No other problems match.</p>}
          <div><button className="btn2" type="button" onClick={() => { setSearching(false); setQ(""); }}>Cancel</button></div>
        </div>
      ) : (
        <div className="row-gap">
          <button className="btn2" type="button" onClick={() => setSearching(true)}>Link to a problem…</button>
          <Link className="btn2" to={`/problems/new?ticket=${ticket.id}`}>Create problem from this ticket</Link>
        </div>
      )}
    </section>
  );
}
