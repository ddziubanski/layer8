// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { when } from "../format";
import { SlaChip, StatusChip, TicketPriorityChip } from "./Chips";

/** Quick details of one ticket, for the Ctrl K lookup: enough to answer "what's this one?" without opening it. */
export function TicketPreview({ id }: { id: number }) {
  const ticket = useQuery({ queryKey: ["ticket", id], queryFn: () => api.ticket(id), staleTime: 30_000, retry: false });
  const comments = useQuery({ queryKey: ["ticket", id, "comments"], queryFn: () => api.comments(id), staleTime: 30_000, enabled: ticket.isSuccess });
  const problems = useQuery({ queryKey: ["ticket", id, "problems"], queryFn: () => api.problems.forTicket(id), staleTime: 30_000, enabled: ticket.isSuccess });
  if (ticket.error) return <aside className="ticket-preview" aria-label="Ticket preview"><p className="muted">{ticket.error.message}</p></aside>;
  const t = ticket.data;
  if (!t) return <aside className="ticket-preview" aria-label="Ticket preview"><p className="muted">Loading ticket…</p></aside>;
  const last = comments.data?.at(-1);
  const workaround = problems.data?.problems.find((p) => p.workaround);
  const facts: [string, string][] = [
    ["Requester", t.requester.display_name],
    ["Queue", t.queue.name],
    ["Assigned to", t.assignee?.display_name ?? "Unassigned"],
    ["Opened", when(t.created_at)],
    ["Updated", when(t.updated_at)],
    ...(t.computer ? [["Computer", t.computer.name] as [string, string]] : []),
  ];
  return (
    <aside className="ticket-preview" aria-label="Ticket preview">
      <p className="muted small" style={{ margin: 0 }}>{t.label}</p>
      <h3 style={{ margin: 0 }}>{t.subject}</h3>
      <div className="row-gap"><StatusChip status={t.status} /><TicketPriorityChip ticket={t} /><SlaChip ticket={t} /></div>
      <dl className="ticket-preview-facts">
        {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      {t.description && <p className="ticket-preview-text">{t.description}</p>}
      {workaround && (
        <p className="small" style={{ margin: 0 }}><strong>Workaround ({workaround.label}):</strong> {workaround.workaround}</p>
      )}
      {last && (
        <div className="stack" style={{ gap: 2 }}>
          <span className="muted small">Latest {last.is_internal ? "internal note" : "reply"} · {last.author?.display_name ?? "Someone"} · {when(last.created_at)}</span>
          <p className="ticket-preview-text">{last.body}</p>
        </div>
      )}
      <p className="muted small" style={{ margin: 0 }}>Enter opens the ticket.</p>
    </aside>
  );
}
