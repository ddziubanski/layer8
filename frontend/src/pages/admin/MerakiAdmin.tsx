import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, type Me } from "../../api/client";
import { ME_KEY } from "../../queryClient";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

export function MerakiAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["meraki-status"], queryFn: api.meraki.status, refetchInterval: 10000, enabled: me.role === "admin" });
  const test = useMutation({ mutationFn: api.meraki.test });
  const sync = useMutation({ mutationFn: () => api.system.runJob("meraki") });
  if (me.role !== "admin") return <p>Meraki is managed by admins.</p>;
  const s = status.data;
  return <div className="stack">
    <div className="page-head"><h1>Meraki</h1></div>
    <section className="card stack" aria-label="Meraki discovery">
      <div className="lbl">Network context for the service desk</div>
      <p className="muted">Read client observations from selected networks. See the last reported switch or access point, port, VLAN and SSID on IPAM records, devices and tickets. These are cached observations; they do not change documented addresses or Meraki configuration.</p>
      {s && <p>{s.clients} cached clients · {s.networks.length} selected networks · {s.enabled ? "Discovery enabled" : "Discovery off"}</p>}
      {s?.last_sync_at && <p className={s.last_sync_ok === false ? "error" : "muted"}>Last read {when(s.last_sync_at)}: {s.last_sync_ok === false ? s.last_sync_error : s.last_sync_summary}</p>}
      {status.error && <p role="alert" className="error">{status.error.message}</p>}
      <div className="row-gap">
        <button className="btn2" disabled={!s?.configured || test.isPending} onClick={() => test.mutate()}>Test selected networks</button>
        <button className="btn" disabled={!s?.enabled || !s.configured || sync.isPending} onClick={() => sync.mutate()}>Read Meraki now</button>
        <Link to="/ipam/meraki">View client observations</Link>
      </div>
      {test.data && <p role="status">Connected: {test.data.networks.join(", ")}</p>}
      {sync.isSuccess && <p role="status">Discovery requested. Follow progress in <Link to="/settings/status">worker status</Link>.</p>}
      {(test.error || sync.error) && <p role="alert" className="error">{(test.error || sync.error)?.message}</p>}
    </section>
    <SettingsForm me={me} section="meraki" title="Connection and discovery" embedded onSaved={() => { test.reset(); sync.reset(); void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }} intro="Save a dedicated read-only Dashboard API key and explicit network IDs. OAuth authorization is planned as a separate connection option." />
  </div>;
}
