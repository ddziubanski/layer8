// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, type Ticket } from "../../api/client";
import { MerakiContext } from "../../components/MerakiContext";
import { NetworkContext } from "../../components/NetworkContext";

function DeviceNetwork({ id, name }: { id: number; name: string }) {
  const ips = useQuery({ queryKey: ["device-ips", id], queryFn: () => api.ipam.forDevice(id) });
  return <section className="card stack" aria-label={`Network for ${name}`}>
    <Link to={`/devices/${id}`}><strong>{name}</strong></Link>
    {ips.isPending && <p className="muted small">Reading cached network context…</p>}
    {ips.error && <p className="error" role="alert">Could not read network context.</p>}
    {ips.data?.length === 0 && <p className="muted small">No IPAM records match this device.</p>}
    {ips.data?.map(({ address }) => <div key={address.id}>
      <Link to={`/ipam/addresses?q=${encodeURIComponent(address.address)}`}>{address.address}</Link><span className="muted small"> · IPAM record</span>
      {address.meraki && <MerakiContext observation={address.meraki} />}
      {!!address.observations?.length && <NetworkContext observations={address.observations} />}
    </div>)}
  </section>;
}

/** Cached context only: no scan, diagnostic or vendor mutation is launched from this drawer. */
export function TicketNetworkContext({ ticket, includeComputer, includeDevices }: { ticket: Ticket; includeComputer: boolean; includeDevices: boolean }) {
  const linked = useQuery({ queryKey: ["ticket-devices", ticket.id], queryFn: () => api.devices.forTicket(ticket.id), enabled: includeDevices });
  const devices = new Map<number, string>();
  if (includeComputer && ticket.computer) devices.set(ticket.computer.id, ticket.computer.name);
  if (includeDevices) for (const link of linked.data ?? []) devices.set(link.device.id, link.device.name);
  return <div className="stack">
    <div><h3>Network context</h3><p className="muted small">Read-only cached observations, separate from documented IP records. Use Devices to manage links.</p></div>
    {linked.error && <p role="alert" className="error">Could not read linked devices.</p>}
    {!devices.size && (!includeDevices || !linked.isPending) && <p className="muted">Link a computer or device to see network context.</p>}
    {[...devices].map(([id, name]) => <DeviceNetwork key={id} id={id} name={name} />)}
  </div>;
}
