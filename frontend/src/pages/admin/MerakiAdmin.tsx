import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, type Me } from "../../api/client";
import { ME_KEY } from "../../queryClient";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

const DELIVERY_OUTCOMES: Record<string, string> = {
  created: "Ticket created", ongoing: "Existing ticket", recovery: "Recovery noted",
  stale: "Older alert ignored", unmatched_recovery: "Recovery without an active ticket",
};

export function MerakiAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const connect = useMutation({ mutationFn: api.meraki.connect, onSuccess: ({ url }) => window.location.assign(url) });
  const disconnect = useMutation({ mutationFn: api.meraki.disconnect, onSuccess: () => { void status.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); } });
  const status = useQuery({ queryKey: ["meraki-status"], queryFn: api.meraki.status, refetchInterval: 10000, enabled: me.role === "admin" });
  const test = useMutation({ mutationFn: api.meraki.test });
  const webhooks = useQuery({ queryKey: ["meraki-webhooks"], queryFn: api.meraki.webhooks, refetchInterval: 10000, enabled: me.role === "admin" });
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
      <p className="muted">Authorize this server to read network configuration and client telemetry. Discovery reads only the network IDs you save below. OAuth is currently available for the meraki.com region. Enabling diagnostics also requires switch:telemetry:read and wireless:telemetry:read in the application registration; reconnect after changing that option.</p>
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
    <section className="card stack" aria-label="Meraki webhook intake">
      <div className="page-head"><h2>Device outage alerts</h2><span className="muted">{webhooks.isError ? "Status unavailable" : !webhooks.data ? "Checking intake…" : webhooks.data.enabled ? (webhooks.data.configured ? "Intake enabled" : "Setup incomplete") : "Intake off"}</span></div>
      <p className="muted">Create a ticket when a selected device stops reporting. Recovery adds an internal note for technician review. Configure the default Meraki webhook payload, a separate shared secret, organization IDs and selected networks below. Discovery credentials are independent.</p>
      {webhooks.data?.endpoint && <p>Webhook receiver: <code style={{ overflowWrap: "anywhere" }}>{webhooks.data.endpoint}</code></p>}
      {webhooks.error && <p role="alert" className="error">Could not read webhook delivery history.</p>}
      {webhooks.data && !webhooks.data.endpoint && <p className="muted">Save an HTTPS portal address in General settings to show the receiver URL.</p>}
      <p className="muted">Supported alerts: stopped_reporting and started_reporting. Duplicate deliveries are ignored. Other alert types do not create tickets.</p>
      {webhooks.data && (webhooks.data.deliveries.length ? <div className="stack" aria-label="Recent webhook deliveries">{webhooks.data.deliveries.map((d, i) => <div key={i} className="row-gap">
        <strong>{d.serial}</strong><span>{DELIVERY_OUTCOMES[d.outcome] ?? d.outcome}</span>
        <span className="muted">{d.network_id} · occurred {when(d.occurred_at)} · received {when(d.received_at)}</span>
        {d.ticket_id && <Link to={`/tickets/${d.ticket_id}`}>Ticket #{d.ticket_id}</Link>}
      </div>)}</div> : <p className="muted">No accepted availability alerts yet.</p>)}
    </section>
    <SettingsForm me={me} section="meraki" title="Connection, discovery and alerts" embedded onSaved={() => { test.reset(); sync.reset(); connect.reset(); disconnect.reset(); void status.refetch(); void webhooks.refetch(); void queryClient.invalidateQueries({ queryKey: ME_KEY }); }} intro="Save a dedicated read-only Dashboard API key and explicit network IDs. Or choose OAuth, save your application credentials, and connect above. Existing API keys remain available. Webhook intake is separately opt-in." />
  </div>;
}
