// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { DeviceIps } from "../../components/DeviceIps";
import { DevicePicker } from "../../components/DevicePicker";
import { PatchStatus } from "../../components/PatchStatus";
import { when } from "../../format";

/** The ticket's devices: linked ones, the requester's own to link in one click, and a search. */
export function DevicesCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket-devices", ticket.id];
  const linked = useQuery({ queryKey: key, queryFn: () => api.devices.forTicket(ticket.id) });
  const requesterId = ticket.requester?.id;
  const theirs = useQuery({
    queryKey: ["devices", "assigned", requesterId],
    queryFn: () => api.devices.list({ assigned_user_id: requesterId, limit: 10 }),
    enabled: requesterId !== undefined,
  });
  const done = {
    onSuccess: (links: Awaited<ReturnType<typeof api.devices.forTicket>>) => queryClient.setQueryData(key, links),
    onSettled: onChanged,
  };
  const link = useMutation({ mutationFn: (deviceId: number) => api.devices.link(ticket.id, deviceId), ...done });
  const unlink = useMutation({ mutationFn: (deviceId: number) => api.devices.unlink(ticket.id, deviceId), ...done });
  const linkedIds = (linked.data ?? []).map((l) => l.device.id);
  const suggestions = (theirs.data?.items ?? []).filter((d) => !linkedIds.includes(d.id));
  const error = link.error ?? unlink.error ?? linked.error;

  return (
    <div className="card stack" style={{ gap: 10 }} aria-label="Devices" role="region">
      <div className="lbl" style={{ margin: 0 }}>
        Devices
      </div>
      {linked.data?.length === 0 && <span className="muted small">None linked.</span>}
      {linked.data?.map(({ device: d }) => (
        <div key={d.id} className="row-gap" style={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "nowrap" }}>
          <span style={{ flex: 1, minWidth: 0 }}>
            <Link to={`/devices/${d.id}`}>{d.name}</Link>
            <span className="muted small">
              {" "}
              {[d.os, d.last_logon_at && `seen ${when(d.last_logon_at)}`, !d.is_active && "gone from AD"].filter(Boolean).join(" · ")}
            </span>
            <DeviceIps deviceId={d.id} compact />
            <PatchStatus deviceId={d.id} compact />
          </span>
          <button className="linkish small" type="button" disabled={unlink.isPending} onClick={() => unlink.mutate(d.id)} aria-label={`Unlink ${d.name}`}>
            Unlink
          </button>
        </div>
      ))}
      {suggestions.length > 0 && (
        <div className="small">
          <span className="muted">{ticket.requester?.display_name}'s: </span>
          {suggestions.map((d) => (
            <button key={d.id} className="btn2" style={{ minHeight: 0, padding: "4px 8px", marginRight: 6 }} type="button" disabled={link.isPending} onClick={() => link.mutate(d.id)}>
              + {d.name}
            </button>
          ))}
        </div>
      )}
      <DevicePicker label="Link a device" exclude={linkedIds} onPick={(d) => link.mutate(d.id)} />
      {error && (
        <p className="error small" role="alert" style={{ margin: 0 }}>
          {error.message}
        </p>
      )}
    </div>
  );
}
