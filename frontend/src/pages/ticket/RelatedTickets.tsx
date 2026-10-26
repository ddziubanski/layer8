// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { StatusChip } from "../../components/Chips";

/** Open tickets that may be the same problem, with why; linking one is the technician's call. */
export function RelatedTickets({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket", ticket.id, "related"];
  const related = useQuery({ queryKey: key, queryFn: () => api.related(ticket.id) });
  // Which suggestion is being linked, and which way round.
  const [linking, setLinking] = useState<{ id: number; label: string; under: "this" | "that" } | null>(null);
  const link = useMutation({
    mutationFn: (l: NonNullable<typeof linking>) =>
      l.under === "this" ? api.updateTicket(l.id, { parent_id: ticket.id }) : api.updateTicket(ticket.id, { parent_id: l.id }),
    onSuccess: () => {
      setLinking(null);
      queryClient.invalidateQueries({ queryKey: key });
      queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "children"] });
      onChanged();
    },
  });
  const list = related.data ?? [];

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Related open tickets">
      <div className="lbl" style={{ margin: 0 }}>Related open tickets</div>
      {related.isPending && <p className="muted small" style={{ margin: 0 }}>Looking…</p>}
      {related.error && <p className="error small" style={{ margin: 0 }}>{related.error.message}</p>}
      {related.data && list.length === 0 && (
        <p className="muted small" style={{ margin: 0 }}>None found. Open tickets sharing a device, system or VM, or words in the subject, show here.</p>
      )}
      {list.map(({ ticket: r, reasons }) => (
        <div key={r.id} className="related-row">
          <div className="row-gap">
            <Link to={`/tickets/${r.id}`} className="spacer">
              {r.label} {r.subject}
            </Link>
            <StatusChip status={r.status} />
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            {reasons.join(" · ")} · {r.queue.name} · {r.assignee?.display_name ?? "Unassigned"}
          </p>
          {linking?.id === r.id ? (
            <div className="row-gap" role="group" aria-label={`Link ${r.label}`}>
              <span className="small spacer">
                {linking.under === "this" ? `Make ${r.label} part of this ticket?` : `Make this ticket part of ${r.label}?`} Nothing else changes; it shows in the child tickets.
              </span>
              <button className="btn" type="button" disabled={link.isPending} onClick={() => link.mutate(linking)}>Link</button>
              <button className="btn2" type="button" disabled={link.isPending} onClick={() => setLinking(null)}>Cancel</button>
            </div>
          ) : (
            <div className="row-gap">
              <button className="linkish small" type="button" onClick={() => setLinking({ id: r.id, label: r.label, under: "this" })}>Part of this ticket</button>
              {!ticket.parent && (
                <button className="linkish small" type="button" onClick={() => setLinking({ id: r.id, label: r.label, under: "that" })}>This is part of {r.label}</button>
              )}
            </div>
          )}
          {link.error && linking?.id === r.id && <p className="error small" style={{ margin: 0 }}>{link.error.message}</p>}
        </div>
      ))}
    </section>
  );
}
