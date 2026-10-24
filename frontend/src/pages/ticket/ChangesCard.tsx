// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { ChangeStatusChip, RiskChip } from "../../components/ChangeChips";

/** Changes linked to the ticket, and raising one for it. */
export function ChangesCard({ ticket }: { ticket: Ticket }) {
  const changes = useQuery({ queryKey: ["ticket", ticket.id, "changes"], queryFn: () => api.changes.forTicket(ticket.id) });
  return (
    <div className="card stack" style={{ gap: 8 }} role="region" aria-label="Changes">
      <div className="lbl" style={{ margin: 0 }}>
        Changes
      </div>
      {changes.data?.length === 0 && <span className="muted small">None linked.</span>}
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
      <Link className="small" to="/changes/new" state={{ ticketId: ticket.id }}>
        New change for this ticket
      </Link>
    </div>
  );
}
