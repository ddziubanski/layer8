// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Me } from "../../api/client";
import { when } from "../../format";
import { ME_KEY } from "../../queryClient";
import { SettingsForm } from "./SettingsForm";

/** Settings > UniFi: connection, what's read, outage tickets and guest vouchers. */
export function UnifiAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["unifi-status"], queryFn: api.unifi.status, refetchInterval: 10000, enabled: me.role === "admin" });
  const test = useMutation({ mutationFn: api.unifi.test });
  const sync = useMutation({ mutationFn: () => api.system.runJob("unifi") });
  if (me.role !== "admin") return <p>UniFi is managed by admins.</p>;
  const s = status.data;
  return (
    <div className="stack">
      <div className="page-head"><h1>UniFi</h1></div>
      <section className="card stack" aria-label="UniFi status">
        <p className="muted" style={{ margin: 0 }}>
          Read Ubiquiti UniFi networks: gateways, switches and access points with their state, and the clients UniFi sees (MAC, IP, access point or switch, network), shown on IP records, devices and tickets. Read only, apart from guest vouchers, which have their own switch and permission. Step by step: docs/unifi.md.
        </p>
        {s && <p style={{ margin: 0 }}>{s.clients} clients · {s.devices} UniFi devices{s.offline ? ` · ${s.offline} offline` : ""} · {s.enabled ? (s.configured ? `Reading (${s.mode === "cloud" ? "cloud" : "local console"})` : "Setup incomplete") : "Off"}</p>}
        {s?.last_sync_at && <p className={s.last_sync_ok === false ? "error" : "muted"} style={{ margin: 0 }}>Last read {when(s.last_sync_at)}: {s.last_sync_ok === false ? s.last_sync_error : s.last_sync_summary}</p>}
        <div className="row-gap">
          <button className="btn2" type="button" disabled={test.isPending} onClick={() => test.mutate()}>Test connection</button>
          <button className="btn" type="button" disabled={!s?.configured || sync.isPending} onClick={() => sync.mutate()}>Read UniFi now</button>
          {s?.enabled && <Link to="/ipam/unifi">See equipment and clients</Link>}
        </div>
        {test.data && (
          <div role="status" className="small">
            {test.data.consoles.length > 0 && <p style={{ margin: 0 }}>Consoles this key can see: {test.data.consoles.join(", ")}</p>}
            <p style={{ margin: 0 }}>Sites that will be read: {test.data.sites.length ? test.data.sites.join(", ") : "none (choose consoles, or check the site IDs)"}</p>
          </div>
        )}
        {sync.isSuccess && <p role="status">Reading. Follow it in <Link to="/settings/status">Settings &gt; Status</Link>.</p>}
        {(test.error || sync.error || status.error) && <p role="alert" className="error">{(test.error || sync.error || status.error)?.message}</p>}
      </section>
      <SettingsForm
        me={me}
        section="unifi"
        title="Connection and options"
        embedded
        intro="Cloud needs only an API key from unifi.ui.com and the console IDs (Test connection lists them). Local needs the console's address, a key from its Integrations page and, usually, its CA certificate."
        onSaved={() => { test.reset(); sync.reset(); void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }}
      />
      <p className="muted small">
        UniFi API references: Ubiquiti's developer documentation (developer.ui.com) and the &ldquo;UniFi best practices&rdquo; guide by uchkunr (CC BY 4.0).
      </p>
    </div>
  );
}
