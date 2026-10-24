// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";
import { StatusChip } from "../components/Chips";
import { when } from "../format";
import { usePortalPath } from "./base";

export function MyTickets() {
  const p = usePortalPath();
  const [closed, setClosed] = useState(false);
  const tickets = useQuery({ queryKey: ["portal", "tickets", closed], queryFn: () => api.portal.tickets(closed), refetchInterval: 60_000 });

  return (
    <section>
      <WorkspaceTitle title="My tickets" area="YOUR REQUESTS" />
      <div className="views" role="group" aria-label="Show">
        <button type="button" aria-pressed={!closed} onClick={() => setClosed(false)}>
          Open
        </button>
        <button type="button" aria-pressed={closed} onClick={() => setClosed(true)}>
          Resolved and closed
        </button>
      </div>
      {tickets.error && <p className="error">{tickets.error.message}</p>}
      {tickets.data && tickets.data.items.length === 0 && (
        <div className="card empty">
          {closed ? "Nothing resolved yet." : "You have no open tickets."} <Link to={p("/")}>Make a request</Link>
        </div>
      )}
      <ul className="ticket-list">
        {tickets.data?.items.map((t) => (
          <li key={t.id} className="card">
            <div className="row-gap">
              <Link to={p(`/tickets/${t.id}`)} className="spacer ticket-link">
                {t.label} {t.subject}
              </Link>
              <StatusChip status={t.status} />
            </div>
            <div className="muted small">
              {t.queue_name} · updated {when(t.updated_at)}
              {t.assignee_name ? ` · ${t.assignee_name} is on it` : ""}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
