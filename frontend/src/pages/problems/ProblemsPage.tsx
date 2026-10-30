// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, type Problem, type ProblemIn, type ProblemSummary, type ProblemUpdate, type User } from "../../api/client";
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { when } from "../../format";

const STATUS: Record<Problem["status"], string> = {
  open: "Open",
  investigating: "Investigating",
  known_error: "Known error",
  resolved: "Resolved",
  closed: "Closed",
};
const STATUS_TONE: Record<Problem["status"], string> = {
  open: "n",
  investigating: "a",
  known_error: "b",
  resolved: "g",
  closed: "",
};
const STATUSES = Object.keys(STATUS) as Problem["status"][];
const BLANK: ProblemIn = { title: "", description: "", status: "open", root_cause: "", workaround: "", resolution: "", owner_id: null, ticket_ids: [] };

function Status({ status }: { status: Problem["status"] }) {
  return <span className={`chip ${STATUS_TONE[status]}`}>{STATUS[status]}</span>;
}

export function ProblemsPage() {
  const { problemId } = useParams();
  if (problemId !== undefined) return <ProblemRecord problemId={problemId} />;
  return <ProblemList />;
}

function ProblemList() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const problems = useQuery({ queryKey: ["problems", q, status], queryFn: () => api.problems.list({ q, status: status || undefined }) });
  return <>
    <div className="page-head row-gap">
      <WorkspaceTitle title="Problems" area="INVESTIGATION" />
      <Link className="btn" to="/problems/new">New problem</Link>
    </div>
    <div className="stack">
      <div className="row-gap">
        <input type="search" style={{ maxWidth: 420 }} placeholder="Search problems" aria-label="Search problems" value={q} onChange={(event) => setQ(event.target.value)} />
        <select aria-label="Problem status" value={status} onChange={(event) => setStatus(event.target.value)} style={{ width: "auto" }}>
          <option value="">All statuses</option>
          {STATUSES.map((value) => <option key={value} value={value}>{STATUS[value]}</option>)}
        </select>
      </div>
      {problems.error && <p className="error" role="alert">{problems.error.message}</p>}
      <div className="table-wrap">
        <table className="grid">
          <thead><tr><th>Problem</th><th>Status</th><th>Owner</th><th>Tickets</th><th>Updated</th></tr></thead>
          <tbody>
            {problems.data?.map((problem) => <ProblemRow key={problem.id} problem={problem} />)}
            {problems.data?.length === 0 && <tr><td colSpan={5} className="muted">{q ? "Nothing matches." : "No problems yet."}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  </>;
}

function ProblemRow({ problem }: { problem: ProblemSummary }) {
  return <tr>
    <td><Link to={`/problems/${problem.id}`}>{problem.label} {problem.title}</Link></td>
    <td><Status status={problem.status} /></td>
    <td className="small">{problem.owner?.display_name ?? "Unassigned"}</td>
    <td>{problem.ticket_count}</td>
    <td className="small">{when(problem.updated_at)}</td>
  </tr>;
}

function ProblemRecord({ problemId }: { problemId: string }) {
  const isNew = problemId === "new";
  const id = Number(problemId);
  const problem = useQuery({ queryKey: ["problem", id], queryFn: () => api.problems.get(id), enabled: !isNew && Number.isSafeInteger(id) && id > 0 });
  if (isNew) return <ProblemEditor />;
  if (!Number.isSafeInteger(id) || id < 1) return <p className="error">Problem not found.</p>;
  if (problem.error) return <p className="error" role="alert">{problem.error.message}</p>;
  if (!problem.data) return <p className="muted">Loading problem…</p>;
  return <ProblemEditor key={problem.data.updated_at} problem={problem.data} />;
}

function inputFor(problem?: Problem): ProblemIn {
  return problem ? {
    title: problem.title,
    description: problem.description,
    status: problem.status,
    root_cause: problem.root_cause,
    workaround: problem.workaround,
    resolution: problem.resolution,
    owner_id: problem.owner?.id ?? null,
    ticket_ids: problem.tickets.map((ticket) => ticket.id),
  } : BLANK;
}

function ProblemEditor({ problem }: { problem?: Problem }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ProblemIn>(() => inputFor(problem));
  const [ticketNumber, setTicketNumber] = useState("");
  const [ticketError, setTicketError] = useState("");
  const [linkedTickets, setLinkedTickets] = useState<Problem["tickets"]>(problem?.tickets ?? []);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const save = useMutation({
    mutationFn: () => problem ? api.problems.update(problem.id, draft) : api.problems.create(draft),
    onSuccess: (saved) => {
      queryClient.setQueryData(["problem", saved.id], saved);
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      if (!problem) navigate(`/problems/${saved.id}`);
    },
  });
  const addNote = useMutation({
    mutationFn: () => api.problems.addUpdate(problem!.id, { body: note }),
    onSuccess: () => {
      setNote("");
      setNoteError("");
      void queryClient.invalidateQueries({ queryKey: ["problem", problem!.id] });
    },
  });

  async function addTicket() {
    const id = Number(ticketNumber.trim().replace(/^#/, ""));
    if (!Number.isSafeInteger(id) || id < 1) {
      setTicketError("Enter a ticket number.");
      return;
    }
    if ((draft.ticket_ids ?? []).includes(id)) {
      setTicketError("That ticket is already linked.");
      return;
    }
    try {
      const ticket = await api.ticket(id);
      setLinkedTickets((current) => [...current, { id: ticket.id, label: ticket.label, subject: ticket.subject, status: ticket.status, created_at: ticket.created_at }]);
      setDraft((current) => ({ ...current, ticket_ids: [...(current.ticket_ids ?? []), id] }));
      setTicketNumber("");
      setTicketError("");
    } catch (error) {
      setTicketError(error instanceof Error ? error.message : "Ticket not found or not visible to you.");
    }
  }

  function removeTicket(ticketId: number) {
    setLinkedTickets((current) => current.filter((ticket) => ticket.id !== ticketId));
    setDraft((current) => ({ ...current, ticket_ids: (current.ticket_ids ?? []).filter((id) => id !== ticketId) }));
  }

  function submitNote(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!note.trim()) {
      setNoteError("Enter an update.");
      return;
    }
    addNote.mutate();
  }

  return <>
    <div className="page-head row-gap">
      <div className="spacer">
        <div className="small"><Link to="/problems">Problems</Link></div>
        <p className="workspace-area">PROBLEM WORKSPACE</p>
        <h1 style={{ marginTop: 4 }}>{problem ? <>{problem.label} {problem.title} <Status status={problem.status} /></> : "New problem"}</h1>
      </div>
    </div>
    <div className="stack">
      <form className="stack" aria-label="Problem details" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <label className="field">Title
          <input required maxLength={300} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
        </label>
        <label className="field">Description
          <textarea rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
        </label>
        <div className="row-gap">
          <label className="field">Status
            <select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as Problem["status"] })}>
              {STATUSES.map((value) => <option key={value} value={value}>{STATUS[value]}</option>)}
            </select>
          </label>
          <label className="field">Owner
            <select value={draft.owner_id ?? ""} onChange={(event) => setDraft({ ...draft, owner_id: event.target.value ? Number(event.target.value) : null })}>
              <option value="">Unassigned</option>
              {(users.data ?? []).filter((user: User) => user.is_active && user.role !== "requester").map((user) => <option key={user.id} value={user.id}>{user.display_name}</option>)}
            </select>
          </label>
        </div>
        <label className="field">Root cause
          <textarea rows={3} value={draft.root_cause} onChange={(event) => setDraft({ ...draft, root_cause: event.target.value })} />
        </label>
        <label className="field">Workaround
          <textarea rows={3} value={draft.workaround} onChange={(event) => setDraft({ ...draft, workaround: event.target.value })} />
        </label>
        <label className="field">Resolution
          <textarea rows={3} value={draft.resolution} onChange={(event) => setDraft({ ...draft, resolution: event.target.value })} />
        </label>
        {save.error && <p className="error" role="alert">{save.error.message}</p>}
        <div className="row-gap">
          <button className="btn" type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : problem ? "Save problem" : "Create problem"}</button>
          <Link className="btn2" to="/problems">Cancel</Link>
        </div>
      </form>

      <section className="stack" aria-labelledby="linked-tickets-heading">
        <div className="row-gap"><h2 id="linked-tickets-heading" className="spacer">Linked tickets</h2><span className="muted small">{linkedTickets.length} linked</span></div>
        <form className="row-gap" onSubmit={(event) => { event.preventDefault(); void addTicket(); }}>
          <label className="field spacer">Ticket number
            <input inputMode="numeric" value={ticketNumber} onChange={(event) => setTicketNumber(event.target.value)} placeholder="e.g. 4821" />
          </label>
          <button className="btn2" type="submit">Link ticket</button>
        </form>
        {ticketError && <p className="error" role="alert">{ticketError}</p>}
        {linkedTickets.length ? <ul className="stack">
          {linkedTickets.map((ticket) => <li key={ticket.id} className="row-gap">
            <Link className="spacer" to={`/tickets/${ticket.id}`}>{ticket.label} {ticket.subject}</Link>
            <button type="button" className="btn2" aria-label={`Unlink ${ticket.label}`} onClick={() => removeTicket(ticket.id)}>Unlink</button>
          </li>)}
        </ul> : <p className="muted">No tickets linked.</p>}
      </section>

      {problem && <section className="stack" aria-labelledby="updates-heading">
        <div className="row-gap"><h2 id="updates-heading" className="spacer">Investigation updates</h2><span className="muted small">Internal to staff</span></div>
        <form className="stack" onSubmit={submitNote}>
          <label className="field">Add an internal update
            <textarea rows={3} value={note} onChange={(event) => { setNote(event.target.value); setNoteError(""); }} />
          </label>
          {(noteError || addNote.error) && <p className="error" role="alert">{noteError || addNote.error?.message}</p>}
          <div><button className="btn2" type="submit" disabled={addNote.isPending}>{addNote.isPending ? "Adding…" : "Add update"}</button></div>
        </form>
        <div className="stack" aria-label="Problem history">
          {problem.updates.map((update) => <UpdateRow key={update.id} update={update} />)}
        </div>
      </section>}
    </div>
  </>;
}

function UpdateRow({ update }: { update: ProblemUpdate }) {
  return <article className="card stack" style={{ gap: 4 }}>
    <div className="row-gap"><strong className="spacer">{update.type === "note" ? update.author?.display_name ?? "Former staff member" : eventLabel(update.type)}</strong><time className="muted small">{when(update.created_at)}</time></div>
    <p>{update.body}</p>
  </article>;
}

function eventLabel(type: string) {
  if (type === "created") return "Problem created";
  if (type === "ticket_linked") return "Ticket linked";
  if (type === "ticket_unlinked") return "Ticket unlinked";
  if (type === "status_changed") return "Status changed";
  return "Problem updated";
}
