// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { ChangeStatusChip, RiskChip } from "../../components/ChangeChips";

/** Changes linked to the ticket, post-change suggestions ("possibly related to CHG-..."), and raising one for it. */
export function ChangesCard({ ticket }: { ticket: Ticket }) {
  const queryClient = useQueryClient();
  const changes = useQuery({ queryKey: ["ticket", ticket.id, "changes"], queryFn: () => api.changes.forTicket(ticket.id) });
  const suggestions = useQuery({
    queryKey: ["ticket", ticket.id, "change-suggestions"],
    queryFn: () => api.changes.suggestionsForTicket(ticket.id),
    retry: false,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "changes"] });
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "change-suggestions"] });
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "context"] });
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id] });
  };
  const link = useMutation({
    mutationFn: (changeId: number) => api.changes.linkTicket(changeId, ticket.id),
    onSuccess: refresh,
  });
  const suggested = suggestions.data ?? [];
  return (
    <div className="card stack" style={{ gap: 8 }} role="region" aria-label="Changes">
      <div className="lbl" style={{ margin: 0 }}>
        Changes
      </div>
      {changes.data?.length === 0 && suggested.length === 0 && <span className="muted small">None linked.</span>}
      {changes.data?.map((c) => (
        <div key={c.id} className="small stack" style={{ gap: 2 }}>
          <Link to={`/changes/${c.id}`}>
            {c.label} {c.title}
          </Link>
          <span className="row-gap" style={{ gap: 4 }}>
            <ChangeStatusChip status={c.status} />
            <RiskChip level={c.risk_level} risk={c.risk} />
            {c.planned_start && <span className="muted">{new Date(c.planned_start).toLocaleString()}</span>}
          </span>
        </div>
      ))}
      {suggested.map(({ change: c, scope }) => (
        <div key={c.id} className="small stack" style={{ gap: 2 }}>
          <div className="row-gap" style={{ gap: 6, flexWrap: "wrap" }}>
            <span className="chip a">Possibly related to {c.label}</span>
            <Link to={`/changes/${c.id}`}>{c.title}</Link>
            <span className="muted">· {scope}</span>
            <button className="btn2 small" type="button" disabled={link.isPending} onClick={() => link.mutate(c.id)}>
              Link
            </button>
          </div>
          <span className="row-gap" style={{ gap: 4 }}>
            <ChangeStatusChip status={c.status} />
            <RiskChip level={c.risk_level} risk={c.risk} />
          </span>
        </div>
      ))}
      <Link className="small" to="/changes/new" state={{ ticketId: ticket.id }}>
        New change for this ticket
      </Link>
    </div>
  );
}
