// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { MerakiDiagnostics } from "../../components/MerakiDiagnostics";
import { MerakiContext } from "../../components/MerakiContext";

export function MerakiClients() {
  const [q, setQ] = useState("");
  const clients = useQuery({ queryKey: ["meraki-clients", q], queryFn: () => api.meraki.clients(q) });
  return <section className="card stack" aria-label="Meraki clients">
    <div className="lbl">Observed on the network</div>
    <p className="muted">Read-only observations from selected Meraki networks. Documented addresses stay in IPAM. Device and ticket context requires an exact, unique MAC match to a documented address.</p>
    <input type="search" aria-label="Search Meraki clients" placeholder="Name, MAC, IP address or network" value={q} onChange={e => setQ(e.target.value)} />
    {clients.isPending && <p role="status">Loading observations…</p>}
    {clients.error && <p className="error" role="alert">{clients.error.message}</p>}
    {clients.data?.length === 0 && <p className="muted">No observations match. An admin can configure and run discovery under Settings → Meraki.</p>}
    <div className="meraki-grid">{clients.data?.map(o => <article className="meraki-client" key={`${o.network_id}:${o.mac}`}>
      <b>{o.description || o.mac}</b><div className="mono small">{o.mac}</div><MerakiContext observation={o} /><MerakiDiagnostics observation={o} />
    </article>)}</div>
    {(clients.data?.length ?? 0) >= 200 && <p className="muted small">Showing the first 200 results. Narrow your search to find more.</p>}
  </section>;
}
