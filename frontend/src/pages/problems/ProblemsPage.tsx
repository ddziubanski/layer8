// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api, type Problem, type ProblemIn, type ProblemSummary, type ProblemUpdate, type Ticket, type User } from "../../api/client";
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { when } from "../../format";
import { ME_KEY } from "../../queryClient";
import { can } from "../../permissions";
import { ActionDialog } from "../../components/ActionDialog";
import { UpdateEveryone } from "../ticket/UpdateEveryone";
import { ProblemStatus, STATUS, STATUSES } from "./status";

const BLANK: ProblemIn = { title: "", description: "", status: "open", root_cause: "", workaround: "", resolution: "", outage_scope: "", owner_id: null, ticket_ids: [] };
const CLOSED = new Set(["resolved", "closed"]);
const NOTICE_STATE: Record<string, string> = { off: "Draft: not shown yet", scheduled: "Scheduled", current: "Showing to users", ended: "Ended" };

type Cluster = Awaited<ReturnType<typeof api.problems.outageClusters>>[number];


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
      <OutageClusters />
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

/** Several devices down at one switch, subnet or site and no problem for it yet: offer one. */
function OutageClusters() {
  const clusters = useQuery({ queryKey: ["problems", "outage-clusters"], queryFn: api.problems.outageClusters, refetchInterval: 60_000 });
  const [open, setOpen] = useState<Cluster | null>(null);
  if (!clusters.data?.length) return null;
  return <section className="card stack" style={{ gap: 8 }} aria-labelledby="outage-clusters-heading">
    <h2 id="outage-clusters-heading" style={{ margin: 0 }}>Outages without a problem</h2>
    <p className="muted small" style={{ margin: 0 }}>Several devices dropped in the same place within half an hour. One problem keeps the tickets, the service notice and the update to everyone together.</p>
    {clusters.data.map((c) => <div key={c.key} className="row-gap" role="group" aria-label={c.scope_label}>
      <div className="spacer">
        <strong>{c.scope_label}</strong>{" "}
        <span className="muted small">{c.devices.length} devices down{c.down_since ? ` since ${when(c.down_since)}` : ""}: {c.devices.join(", ")}</span>
      </div>
      <button className="btn2" type="button" onClick={() => setOpen(c)}>Open a problem…</button>
    </div>)}
    {open && <FromOutageDialog key={open.key} cluster={open} onClose={() => setOpen(null)} />}
  </section>;
}

function FromOutageDialog({ cluster, onClose }: { cluster: Cluster; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false }).data;
  const mayNotice = !!me && can(me, "notices_manage");
  const [title, setTitle] = useState(cluster.suggested_title);
  const [notice, setNotice] = useState(mayNotice);
  const [noticeTitle, setNoticeTitle] = useState(cluster.suggested_notice_title);
  const [noticeMessage, setNoticeMessage] = useState(cluster.suggested_notice_message);
  const create = useMutation({
    mutationFn: () => api.problems.fromOutage({
      title, description: "", workaround: "", outage_scope: cluster.scope_label, ticket_ids: cluster.ticket_ids,
      create_draft_notice: notice && mayNotice, notice_title: noticeTitle, notice_message: noticeMessage,
    }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["problem", saved.id], saved);
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      navigate(`/problems/${saved.id}`);
    },
  });
  return <ActionDialog title={`Problem for ${cluster.scope_label}`} busy={create.isPending} onClose={onClose}>
    <form className="stack" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
      <label className="field">Title
        <input required maxLength={300} value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <p className="muted small" style={{ margin: 0 }}>
        {cluster.ticket_ids.length ? `Links the ${cluster.ticket_ids.length} outage ticket${cluster.ticket_ids.length === 1 ? "" : "s"} you can see. ` : ""}
        New tickets from people at {cluster.scope_label} are offered this problem.
      </p>
      {mayNotice ? <>
        <label className="inline"><input type="checkbox" checked={notice} onChange={(event) => setNotice(event.target.checked)} />Draft a service notice</label>
        {notice && <>
          <label className="field">Notice title
            <input maxLength={200} value={noticeTitle} onChange={(event) => setNoticeTitle(event.target.value)} />
          </label>
          <label className="field">Notice message
            <textarea rows={3} maxLength={4000} value={noticeMessage} onChange={(event) => setNoticeMessage(event.target.value)} />
          </label>
          <p className="muted small" style={{ margin: 0 }}>The notice starts as a draft: nobody sees it until you turn it on in Settings › Notices.</p>
        </>}
      </> : <p className="muted small" style={{ margin: 0 }}>Service notices are drafted by people with the notices permission.</p>}
      {create.error && <p className="error" role="alert">{create.error.message}</p>}
      <div className="row-gap">
        <button className="btn2" type="button" disabled={create.isPending} onClick={onClose}>Cancel</button>
        <button className="btn" type="submit" disabled={create.isPending || !title.trim()}>{create.isPending ? "Opening…" : "Open problem"}</button>
      </div>
    </form>
  </ActionDialog>;
}

function OutageStatus({ problem, onUpdateEveryone }: { problem: Problem; onUpdateEveryone: () => void }) {
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false }).data;
  const active = problem.active_outage_devices ?? [];
  const recovered = problem.recovered_outage_devices ?? [];
  return <section className="card stack" style={{ gap: 8 }} aria-labelledby="outage-heading">
    <div className="row-gap"><h2 id="outage-heading" className="spacer" style={{ margin: 0 }}>Outage</h2>{problem.outage_scope && <span className="muted small">{problem.outage_scope}</span>}</div>
    {active.length > 0 && <p className="error" style={{ margin: 0 }}>Still down: {active.join(", ")}</p>}
    {problem.outage_recovered && <div className="row-gap">
      <p className="spacer" style={{ margin: 0 }}>Back up{recovered.length ? `: ${recovered.join(", ")}` : ""}. Tell everyone affected, and resolve their tickets if it&apos;s fixed.</p>
      <button className="btn" type="button" onClick={onUpdateEveryone}>Update everyone affected…</button>
    </div>}
    {!active.length && !problem.outage_recovered && <p className="muted small" style={{ margin: 0 }}>No tracked devices are down here right now.</p>}
    {problem.notice && <p className="small" style={{ margin: 0 }}>
      Service notice: <strong>{problem.notice.title}</strong> · {NOTICE_STATE[problem.notice.state] ?? problem.notice.state}
      {me && can(me, "notices_manage") && <> · <Link to="/settings/notices">Manage notices</Link></>}
    </p>}
  </section>;
}

function ProblemRow({ problem }: { problem: ProblemSummary }) {
  return <tr>
    <td><Link to={`/problems/${problem.id}`}>{problem.label} {problem.title}</Link></td>
    <td><ProblemStatus status={problem.status} /></td>
    <td className="small">{problem.owner?.display_name ?? "Unassigned"}</td>
    <td>{problem.ticket_count}</td>
    <td className="small">{when(problem.updated_at)}</td>
  </tr>;
}

function ProblemRecord({ problemId }: { problemId: string }) {
  const isNew = problemId === "new";
  const id = Number(problemId);
  // "Create problem from this ticket" opens /problems/new?ticket=<id>: start from that ticket.
  const fromId = Number(useSearchParams()[0].get("ticket"));
  const from = useQuery({ queryKey: ["ticket", fromId], queryFn: () => api.ticket(fromId), enabled: isNew && Number.isSafeInteger(fromId) && fromId > 0 });
  const problem = useQuery({ queryKey: ["problem", id], queryFn: () => api.problems.get(id), enabled: !isNew && Number.isSafeInteger(id) && id > 0 });
  if (isNew && fromId > 0) {
    if (from.error) return <p className="error" role="alert">{from.error.message}</p>;
    if (!from.data) return <p className="muted">Loading the ticket…</p>;
    return <ProblemEditor key={`from-${fromId}`} fromTicket={from.data} />;
  }
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
    outage_scope: problem.outage_scope,
    owner_id: problem.owner?.id ?? null,
    ticket_ids: problem.tickets.map((ticket) => ticket.id),
  } : BLANK;
}

function ProblemEditor({ problem, fromTicket }: { problem?: Problem; fromTicket?: Ticket }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false }).data;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.problems.remove(problem!.id),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["problem", problem!.id] });
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      navigate("/problems");
    },
  });
  const [draft, setDraft] = useState<ProblemIn>(() => fromTicket
    ? { ...BLANK, title: fromTicket.subject, description: fromTicket.description ?? "", ticket_ids: [fromTicket.id] }
    : inputFor(problem));
  const [ticketNumber, setTicketNumber] = useState("");
  const [ticketError, setTicketError] = useState("");
  const [linkedTickets, setLinkedTickets] = useState<Problem["tickets"]>(problem?.tickets ?? (fromTicket
    ? [{ id: fromTicket.id, label: fromTicket.label, subject: fromTicket.subject, status: fromTicket.status, created_at: fromTicket.created_at }] : []));
  const [updating, setUpdating] = useState(false);
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

  const suggested = (problem?.suggested_tickets ?? []).filter((s) => !(draft.ticket_ids ?? []).includes(s.ticket.id));
  // Saved links only: the update goes to the tickets the server has linked.
  const openLinked = (problem?.tickets ?? []).filter((t) => !CLOSED.has(t.status)).length;

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
        <h1 style={{ marginTop: 4 }}>{problem ? <>{problem.label} {problem.title} <ProblemStatus status={problem.status} /></> : "New problem"}</h1>
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
        <label className="field">Outage scope
          <input maxLength={255} value={draft.outage_scope ?? ""} placeholder="e.g. Site Branch, Switch SW-2 or Subnet 10.20.0.0/24"
            onChange={(event) => setDraft({ ...draft, outage_scope: event.target.value })} />
        </label>
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
          <Link className="btn2" to={fromTicket ? `/tickets/${fromTicket.id}` : "/problems"}>Cancel</Link>
          {problem && me && can(me, "problem_delete") && <button className="btn2 danger pill-delete" type="button" onClick={() => setConfirmDelete(true)}>Delete problem…</button>}
        </div>
      </form>
      {confirmDelete && problem && <ActionDialog title={`Delete ${problem.label}?`} busy={remove.isPending} onClose={() => setConfirmDelete(false)}>
        <p>This permanently deletes the problem, its root cause, workaround and investigation updates. Its {problem.tickets.length} linked ticket{problem.tickets.length === 1 ? "" : "s"} stay; each one's history notes that the problem was deleted. To keep the record, set its status to Closed instead.</p>
        {remove.error && <p className="error" role="alert">{remove.error.message}</p>}
        <div className="row-gap">
          <button className="btn2" type="button" disabled={remove.isPending} onClick={() => setConfirmDelete(false)}>Cancel</button>
          <button className="btn danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete problem</button>
        </div>
      </ActionDialog>}

      {problem && (problem.outage_scope || problem.notice || (problem.active_outage_devices ?? []).length > 0 || problem.outage_recovered) &&
        <OutageStatus problem={problem} onUpdateEveryone={() => setUpdating(true)} />}

      <section className="stack" aria-labelledby="linked-tickets-heading">
        <div className="row-gap">
          <h2 id="linked-tickets-heading" className="spacer">Linked tickets</h2><span className="muted small">{linkedTickets.length} linked</span>
          {openLinked > 0 && !updating && <button className="btn2" type="button" onClick={() => setUpdating(true)}>Update everyone affected…</button>}
        </div>
        {updating && problem && <UpdateEveryone problemId={problem.id} onDone={() => setUpdating(false)} />}
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
        {suggested.length > 0 && <div className="stack" style={{ gap: 6 }} role="group" aria-label="Part of this outage?">
          <h3 style={{ margin: 0 }}>Part of this outage?</h3>
          {suggested.map(({ ticket, scope }) => <div key={ticket.id} className="row-gap">
            <Link className="spacer" to={`/tickets/${ticket.id}`}>{ticket.label} {ticket.subject}</Link>
            <span className="muted small">{scope.replace(/^Part of this outage · /, "")}</span>
            <button type="button" className="btn2" aria-label={`Link ${ticket.label}`} onClick={() => {
              setLinkedTickets((current) => [...current, ticket]);
              setDraft((current) => ({ ...current, ticket_ids: [...(current.ticket_ids ?? []), ticket.id] }));
            }}>Link</button>
          </div>)}
          <p className="muted small" style={{ margin: 0 }}>Open tickets from people at the same site, switch or subnet. Save the problem to keep the links.</p>
        </div>}
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
  if (type === "notice_drafted") return "Service notice drafted";
  if (type === "broadcast") return "Everyone affected updated";
  return "Problem updated";
}
