// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Me } from "../../api/client";
import { when } from "../../format";
import { ME_KEY } from "../../queryClient";
import { SettingsForm } from "./SettingsForm";

/** Settings > Juniper: EX switches read through a runner (Junos REST API, read only). */
export function JunosAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["junos-status"], queryFn: api.junos.status, refetchInterval: 10000, enabled: me.role === "admin" });
  const read = useMutation({ mutationFn: api.junos.read, onSuccess: () => void status.refetch() });
  if (me.role !== "admin") return <p>Juniper is managed by admins.</p>;
  const s = status.data;
  return (
    <div className="stack">
      <div className="page-head"><h1>Juniper</h1></div>
      <section className="card stack" aria-label="Juniper status">
        <p className="muted" style={{ margin: 0 }}>
          Read Juniper EX switches configured one by one (not Mist): a relay on your LAN asks each switch's Junos REST API for its MAC and ARP tables, so clients show with their switch, port and VLAN beside IP records, devices and tickets. Read only. Step by step: docs/juniper.md.
        </p>
        {s && <p style={{ margin: 0 }}>{s.switches} switches · {s.clients} clients{s.offline ? ` · ${s.offline} not answering` : ""} · {s.enabled ? (s.configured ? "On" : "Setup incomplete") : "Off"}{s.waiting && " · a read is waiting for its relay"}</p>}
        {s?.last_read_at && <p className={s.last_error ? "error" : "muted"} style={{ margin: 0 }}>Last read {when(s.last_read_at)}: {s.last_error || s.last_summary}</p>}
        <div className="row-gap">
          <button className="btn" type="button" disabled={!s?.configured || s.waiting || read.isPending} onClick={() => read.mutate()}>Read the switches now</button>
          {s?.enabled && <Link to="/ipam/junos">See switches and clients</Link>}
        </div>
        {read.isSuccess && <p role="status">Asked a relay to read the switches (run #{read.data.run_id}). Scripts &gt; All runs shows its progress.</p>}
        {(read.error || status.error) && <p role="alert" className="error">{(read.error || status.error)?.message}</p>}
      </section>
      <SettingsForm
        me={me}
        section="junos"
        title="Switches and options"
        embedded
        intro="On each switch: a read-only login class, and the REST API over HTTPS (set system services rest https port 3443, with a server certificate). Then list the switches here."
        onSaved={() => { read.reset(); void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }}
      />
    </div>
  );
}
