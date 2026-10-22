import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { VendorSupport } from "../../components/VendorSupport";

/** Systems the ticket is about, with their vendor's support details at hand. */
export function SystemsCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket-systems", ticket.id];
  const linked = useQuery({ queryKey: key, queryFn: () => api.systems.forTicket(ticket.id) });
  const [q, setQ] = useState("");
  const found = useQuery({ queryKey: ["systems", q], queryFn: () => api.systems.list({ q }), enabled: q.trim().length >= 2 });
  const done = { onSuccess: (links: Awaited<ReturnType<typeof api.systems.forTicket>>) => queryClient.setQueryData(key, links), onSettled: onChanged };
  const link = useMutation({ mutationFn: (id: number) => api.systems.link(ticket.id, id), ...done, onSettled: () => (setQ(""), onChanged()) });
  const unlink = useMutation({ mutationFn: (id: number) => api.systems.unlink(ticket.id, id), ...done });
  const ids = (linked.data ?? []).map((l) => l.system.id);
  const hits = (found.data ?? []).filter((s) => !ids.includes(s.id)).slice(0, 8);

  return (
    <div className="card stack" style={{ gap: 10 }} role="region" aria-label="Systems">
      <div className="lbl" style={{ margin: 0 }}>
        Systems
      </div>
      {linked.data?.length === 0 && <span className="muted small">None linked.</span>}
      {linked.data?.map(({ system: s }) => (
        <div key={s.id} className="stack" style={{ gap: 4 }}>
          <div className="row-gap" style={{ justifyContent: "space-between" }}>
            <Link to="/vendors/systems">
              <strong>{s.name}</strong>
            </Link>
            <button className="linkish small" type="button" disabled={unlink.isPending} onClick={() => unlink.mutate(s.id)} aria-label={`Unlink ${s.name}`}>
              Unlink
            </button>
          </div>
          <VendorSupport system={s} />
        </div>
      ))}
      <label className="field picker">
        Link a system
        <input type="search" placeholder="Name, vendor, IP or device" value={q} onChange={(e) => setQ(e.target.value)} />
        {hits.length > 0 && (
          <ul className="picker-list" role="listbox">
            {hits.map((s) => (
              <li key={s.id} role="option" aria-selected={false} onMouseDown={(e) => (e.preventDefault(), link.mutate(s.id))}>
                {s.name} <span className="muted small">{s.vendor?.name ?? ""}</span>
              </li>
            ))}
          </ul>
        )}
      </label>
      {(link.error ?? unlink.error) && <p className="error small">{(link.error ?? unlink.error)!.message}</p>}
    </div>
  );
}
