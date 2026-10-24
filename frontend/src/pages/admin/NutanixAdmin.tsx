// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Me } from "../../api/client";
import { when } from "../../format";
import { ME_KEY } from "../../queryClient";
import { SettingsForm } from "./SettingsForm";

/** Settings > Nutanix: the Prism Central connection, what's read and power actions from tickets. */
export function NutanixAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["nutanix-status"], queryFn: api.nutanix.status, refetchInterval: 10000, enabled: me.role === "admin" });
  const test = useMutation({ mutationFn: api.nutanix.test });
  const sync = useMutation({ mutationFn: () => api.system.runJob("nutanix") });
  if (me.role !== "admin") return <p>Nutanix is managed by admins.</p>;
  const s = status.data;
  return (
    <div className="stack">
      <div className="page-head"><h1>Nutanix</h1></div>
      <section className="card stack" aria-label="Nutanix status">
        <p className="muted" style={{ margin: 0 }}>
          Read VMs from Nutanix Prism Central: power state, host and cluster, size, IP and MAC addresses. VMs are matched to devices by name, show beside IP records, and can be linked to tickets. Power actions from tickets (restart, power on, power cycle) have their own switch and permissions, and each is noted on the ticket. Step by step: docs/nutanix.md.
        </p>
        {s && <p style={{ margin: 0 }}>{s.vms} VMs · {s.running} running · {s.enabled ? (s.configured ? "Reading" : "Setup incomplete") : "Off"}{s.power_enabled ? " · power actions on" : ""}</p>}
        {s?.last_sync_at && <p className={s.last_sync_ok === false ? "error" : "muted"} style={{ margin: 0 }}>Last read {when(s.last_sync_at)}: {s.last_sync_ok === false ? s.last_sync_error : s.last_sync_summary}</p>}
        <div className="row-gap">
          <button className="btn2" type="button" disabled={!s?.configured || test.isPending} onClick={() => test.mutate()}>Test connection</button>
          <button className="btn" type="button" disabled={!s?.configured || sync.isPending} onClick={() => sync.mutate()}>Read Nutanix now</button>
          {s?.enabled && <Link to="/devices?view=vms">See VMs</Link>}
        </div>
        {test.data && (
          <p role="status" className="small" style={{ margin: 0 }}>
            Signed in. Clusters: {test.data.clusters.length ? test.data.clusters.join(", ") : "none listed (the account may not see clusters)"} · {test.data.vms} VMs visible.
          </p>
        )}
        {sync.isSuccess && <p role="status">Reading. Follow it in <Link to="/settings/status">Settings &gt; Status</Link>.</p>}
        {(test.error || sync.error || status.error) && <p role="alert" className="error">{(test.error || sync.error || status.error)?.message}</p>}
      </section>
      <SettingsForm
        me={me}
        section="nutanix"
        title="Connection and options"
        embedded
        intro="Prism Central pc.2024.1 or later (v4 API). Use an account that may view VMs, clusters and hosts; for power actions it also needs VM power rights."
        onSaved={() => { test.reset(); sync.reset(); void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }}
      />
    </div>
  );
}
