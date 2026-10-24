// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket } from "../../api/client";
import { DeviceIps } from "../../components/DeviceIps";
import { DevicePicker } from "../../components/DevicePicker";
import { PatchStatus } from "../../components/PatchStatus";
import { SoftwareDeployment } from "../../components/SoftwareDeployment";
import { ME_KEY } from "../../queryClient";
import { when } from "../../format";

/** The requester's computer, and quick actions on it: Remote Assistance, scripts, its device page. */
export function ComputerCard({ ticket, onChanged, onRunScript }: { ticket: Ticket; onChanged: () => void; onRunScript: () => void }) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me });
  const pc = ticket.computer;
  const [changing, setChanging] = useState(false);
  const theirs = useQuery({
    queryKey: ["devices", "assigned", ticket.requester.id],
    queryFn: () => api.devices.list({ assigned_user_id: ticket.requester.id, limit: 10 }),
    enabled: changing || !pc,
  });
  const sessionsKey = ["remote-sessions", ticket.id];
  const sessions = useQuery({ queryKey: sessionsKey, queryFn: () => api.remote.list(ticket.id) });
  const set = useMutation({
    mutationFn: (deviceId: number | null) => api.updateTicket(ticket.id, { computer_id: deviceId }),
    onSuccess: () => setChanging(false),
    onSettled: onChanged,
  });
  const remote = useMutation({
    mutationFn: () => api.remote.start(ticket.id),
    onSuccess: (launch) => {
      // Hands over to the launcher on this PC (docs/remote-assistance.md).
      window.location.href = launch.launch_uri;
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: sessionsKey });
      onChanged();
    },
  });
  const close = useMutation({
    mutationFn: api.remote.close,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: sessionsKey });
      onChanged();
    },
  });
  const open = (sessions.data ?? []).filter((s) => !s.ended_at);
  const error = set.error ?? remote.error ?? close.error;
  const choices = (theirs.data?.items ?? []).filter((d) => d.id !== pc?.id);

  return (
    <div className="card stack" style={{ gap: 10 }} role="region" aria-label="Requester's computer">
      <div className="lbl" style={{ margin: 0 }}>
        Requester's computer
      </div>
      {pc && !changing && (
        <>
          <div>
            <Link to={`/devices/${pc.id}`}>
              <strong>{pc.name}</strong>
            </Link>
            <div className="muted small">
              {[pc.os, pc.last_logon_at && `seen ${when(pc.last_logon_at)}`, !pc.is_active && "gone from AD", pc.assigned_user_id !== ticket.requester.id && "not assigned to the requester"]
                .filter(Boolean)
                .join(" · ")}
            </div>
            <DeviceIps deviceId={pc.id} compact />
            <PatchStatus deviceId={pc.id} compact />
          </div>
          <div className="quick-actions">
            <button className="btn" type="button" disabled={remote.isPending} onClick={() => remote.mutate()}>
              Remote Assistance
            </button>
            <button className="btn2" type="button" onClick={onRunScript}>
              Run a script…
            </button>
          </div>
          <SoftwareDeployment me={me.data} deviceId={pc.id} ticketId={ticket.id} onChanged={onChanged} />
          {open.map((s) => (
            <div key={s.id} className="small row-gap">
              <span className="spacer">
                Remote Assistance with {s.target} since {when(s.started_at)}
              </span>
              <button className="linkish small" type="button" disabled={close.isPending} onClick={() => close.mutate(s.id)}>
                Mark ended
              </button>
            </div>
          ))}
          <button className="linkish small" type="button" style={{ justifySelf: "start", alignSelf: "flex-start" }} onClick={() => setChanging(true)}>
            Change computer
          </button>
        </>
      )}
      {(!pc || changing) && (
        <>
          {!pc && <span className="muted small">Not set. Pick it to use Remote Assistance and scripts on it.</span>}
          {choices.length > 0 && (
            <div className="row-gap">
              <span className="muted small">{ticket.requester.display_name}'s:</span>
              {choices.map((d) => (
                <button key={d.id} className="btn2" style={{ minHeight: 0, padding: "4px 8px" }} type="button" disabled={set.isPending} onClick={() => set.mutate(d.id)}>
                  {d.name}
                </button>
              ))}
            </div>
          )}
          <DevicePicker label="Find a computer" exclude={pc ? [pc.id] : []} onPick={(d) => set.mutate(d.id)} />
          <div className="row-gap">
            {changing && (
              <button className="btn2" type="button" onClick={() => setChanging(false)}>
                Cancel
              </button>
            )}
            {changing && pc && (
              <button className="linkish small" type="button" disabled={set.isPending} onClick={() => set.mutate(null)}>
                Clear
              </button>
            )}
          </div>
        </>
      )}
      {error && (
        <p className="error small" role="alert" style={{ margin: 0 }}>
          {error.message}
        </p>
      )}
    </div>
  );
}
