import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, type Me } from "../../api/client";
import { ME_KEY } from "../../queryClient";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

export function MerakiAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const connect = useMutation({ mutationFn: api.meraki.connect, onSuccess: ({ url }) => window.location.assign(url) });
  const disconnect = useMutation({ mutationFn: api.meraki.disconnect, onSuccess: () => { void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); } });
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
    {s?.auth_mode === "oauth" && <section className="card stack" aria-label="Meraki OAuth connection">
      <h2>OAuth connection</h2>
      <p className="muted">Authorize this server to read network configuration and client telemetry. Discovery reads only the network IDs you save below. OAuth is currently available for the meraki.com region.</p>
      {s.oauth?.redirect_uri && <p>Register this exact redirect URI: <code>{s.oauth.redirect_uri}</code></p>}
      <p>{s.oauth?.connected ? "Meraki OAuth connected." : "Save the application credentials and HTTPS portal address, then connect with a Meraki organization administrator."}</p>
      {params.get("meraki_oauth") === "failed" && <p role="alert" className="error">Meraki authorization was not completed. Check your application registration, sign in to Layer 8, and connect again.</p>}
      {params.get("meraki_oauth") === "connected" && s.oauth?.connected && <p role="status">Meraki authorization completed.</p>}
      <div className="row-gap">
        <button type="button" className="btn" disabled={!s.oauth?.configured || !s.networks.length || connect.isPending || disconnect.isPending} onClick={() => connect.mutate()}>{s.oauth?.connected ? "Reconnect Meraki" : "Connect Meraki"}</button>
        <button type="button" className="btn2" disabled={disconnect.isPending || connect.isPending} onClick={() => disconnect.mutate()}>Disconnect Meraki</button>
      </div>
      {disconnect.data && <p role="status">{disconnect.data.revoked ? "Meraki connection removed. Remote access tokens can take up to 10 minutes to expire." : "Local connection removed. Remove this integration in Meraki Dashboard to revoke remote access."}</p>}
      {(connect.error || disconnect.error) && <p role="alert" className="error">{(connect.error || disconnect.error)?.message}</p>}
    </section>}
    <SettingsForm me={me} section="meraki" title="Connection and discovery" embedded onSaved={() => { test.reset(); sync.reset(); connect.reset(); disconnect.reset(); void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }} intro="Save a dedicated read-only Dashboard API key and explicit network IDs. Or choose OAuth, save your application credentials, and connect above. Existing API keys remain available." />
  </div>;
}
