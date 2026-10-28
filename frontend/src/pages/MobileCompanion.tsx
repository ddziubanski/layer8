// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, type Me, type TicketScope } from "../api/client";
import { TicketPriorityChip, StatusChip } from "../components/Chips";
import { when } from "../format";
import { ME_KEY } from "../queryClient";
import { skipSilentWindowsSignIn } from "./Login";
import "./mobile-companion.css";

export function MobileCompanion({ me }: { me: Me }) {
  const id = Number(useParams().ticketId);
  const client = useQueryClient();
  const logout = useMutation({ mutationFn: api.logout, onSettled: () => {
    skipSilentWindowsSignIn();
    client.removeQueries({ predicate: q => q.queryKey[0] !== ME_KEY[0] });
    client.invalidateQueries({ queryKey: ME_KEY });
  } });
  return <div className="mobile-companion">
    <header className="mobile-header"><Link to="/mobile">Layer 8 <span>Companion</span></Link><button type="button" onClick={() => logout.mutate()} disabled={logout.isPending}>Sign out</button></header>
    <main className="mobile-content">
      {id ? <MobileTicket key={id} id={id} /> : <MobileTickets me={me} />}
    </main>
    <nav className="mobile-nav" aria-label="Companion navigation"><Link to="/mobile">Tickets</Link><Link to="/">Full console</Link></nav>
  </div>;
}

function MobileTickets({ me }: { me: Me }) {
  const [scope, setScope] = useState<TicketScope>("mine");
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const tickets = useQuery({ queryKey: ["mobile-tickets", scope, search, offset], queryFn: () => api.tickets({ scope, ...(search ? { q: search } : { view: "open" }), limit: 25, offset }), refetchInterval: 30_000 });
  function submit(e: FormEvent) { e.preventDefault(); setOffset(0); setSearch(typed.trim()); }
  return <>
    <p className="mobile-eyebrow">YOUR SERVICE DESK</p><h1>Hello, {me.display_name}</h1><p>Find a ticket or check your open work.</p>
    <form onSubmit={submit} className="mobile-search"><label htmlFor="mobile-search">Search tickets</label><div><input id="mobile-search" type="search" value={typed} onChange={e => setTyped(e.target.value)} placeholder="Ticket number, subject or notes" /><button type="submit">Search</button></div></form>
    <div className="mobile-scope" role="group" aria-label="Whose tickets">{([{ key: "mine", label: "My tickets" }, { key: "groups", label: "My groups" }, { key: "all", label: "All tickets" }] as const).map(s => <button key={s.key} type="button" aria-pressed={scope === s.key} onClick={() => { setScope(s.key); setOffset(0); }}>{s.label}</button>)}</div>
    <h2>{search ? `Search results for “${search}”` : "Open tickets"}</h2>
    {tickets.isPending && <p>Loading tickets…</p>}{tickets.error && <p role="alert">Could not load tickets: {tickets.error.message}</p>}
    {tickets.data && <><p>{tickets.data.total} {tickets.data.total === 1 ? "ticket" : "tickets"}</p>{tickets.data.items.length === 0 && <p>No tickets found.</p>}
      <div className="mobile-list">{tickets.data.items.map(t => <Link className="mobile-ticket-card" to={`/mobile/tickets/${t.id}`} key={t.id}><span>{t.label} · {t.queue.name}</span><strong>{t.subject}</strong><span>{t.requester.display_name}</span><div><StatusChip status={t.status} /> <TicketPriorityChip ticket={t} /></div></Link>)}</div>
      <div className="mobile-pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><button type="button" disabled={offset + tickets.data.items.length >= tickets.data.total} onClick={() => setOffset(offset + 25)}>Next</button></div></>}
  </>;
}

function MobileTicket({ id }: { id: number }) {
  const client = useQueryClient();
  const ticket = useQuery({ queryKey: ["ticket", id], queryFn: () => api.ticket(id) });
  const comments = useQuery({ queryKey: ["ticket", id, "comments"], queryFn: () => api.comments(id) });
  const [body, setBody] = useState("");
  const [destination, setDestination] = useState("internal");
  const send = useMutation({ mutationFn: () => api.addComment(id, body.trim(), destination === "internal", destination === "email"), onSuccess: () => { setBody(""); setDestination("internal"); client.invalidateQueries({ queryKey: ["ticket", id] }); client.invalidateQueries({ queryKey: ["mobile-tickets"] }); } });
  return <><Link to="/mobile">← Back to tickets</Link>
    {ticket.isPending && <p>Loading ticket…</p>}{ticket.error && <p role="alert">Could not load ticket: {ticket.error.message}</p>}
    {ticket.data && <><p className="mobile-eyebrow">{ticket.data.label} · {ticket.data.queue.name}</p><h1>{ticket.data.subject}</h1><div><StatusChip status={ticket.data.status} /> <TicketPriorityChip ticket={ticket.data} /></div><p>Requester: {ticket.data.requester.display_name}<br />Assigned to: {ticket.data.assignee?.display_name ?? "Unassigned"}</p>
      {ticket.data.merged_into && <p>This ticket was merged into <Link to={`/mobile/tickets/${ticket.data.merged_into.id}`}>{ticket.data.merged_into.label}</Link>.</p>}
      <h2>Conversation</h2><article className="mobile-message"><strong>{ticket.data.requester.display_name}</strong><small>{when(ticket.data.created_at)}</small><p>{ticket.data.description || "No details given."}</p></article>
      {comments.isPending && <p>Loading conversation…</p>}{comments.error && <p role="alert">Could not load conversation: {comments.error.message}</p>}
      {comments.data?.map(c => <article className="mobile-message" key={c.id}><strong>{c.author.display_name} · {c.is_internal ? "Internal note" : c.emailed ? "Emailed reply" : "Portal reply"}</strong><small>{when(c.created_at)}</small><p>{c.body}</p></article>)}
      <p>Attachments and full ticket tools are available in the <Link to={`/tickets/${id}`}>full console</Link>.</p>
      {!ticket.data.merged_into && <form className="mobile-reply" onSubmit={e => { e.preventDefault(); if (body.trim() && !send.isPending) send.mutate(); }}><h2>Add a note or reply</h2><label htmlFor="mobile-destination">Visibility</label><select id="mobile-destination" value={destination} onChange={e => setDestination(e.target.value)} disabled={send.isPending}><option value="internal">Internal note</option><option value="portal">Reply in portal</option><option value="email">Reply in portal and email requester</option></select><label htmlFor="mobile-body">Message</label><textarea id="mobile-body" rows={5} value={body} onChange={e => setBody(e.target.value)} disabled={send.isPending} /><button type="submit" disabled={!body.trim() || send.isPending}>{send.isPending ? "Saving…" : destination === "internal" ? "Add internal note" : "Send reply"}</button>{send.error && <p role="alert">Could not save: {send.error.message}</p>}</form>}
    </>}
  </>;
}
