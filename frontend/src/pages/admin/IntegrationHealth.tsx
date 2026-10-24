// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { when } from "../../format";

const STATES = { ok: ["g", "Healthy"], warning: ["a", "Needs attention"], error: ["r", "Problem"], off: ["n", "Off"], unknown: ["n", "Not checked"] } as const;
const attention = (state: string) => ["warning", "error", "unknown"].includes(state);

/** Mounted only inside the admin-only Status page. Refresh reads local evidence, never probes providers. */
export function IntegrationHealth() {
  const [filter, setFilter] = useState("enabled");
  const health = useQuery({ queryKey: ["integration-health"], queryFn: api.system.integrations, refetchInterval: 30_000 });
  const data = health.data;
  const items = data?.items.filter(item => filter === "all" || (filter === "attention" ? item.enabled && attention(item.state) : item.enabled)) ?? [];
  const problemRelays = data?.relays.filter(relay => relay.state !== "ok") ?? [];
  return <section className="card stack integration-health" id="integrations" aria-labelledby="integration-health-title">
    <header className="row-gap"><div className="spacer"><h2 id="integration-health-title">Integration health</h2><p className="muted small">Saved configuration, worker passes and relay check-ins. Refreshing does not contact providers or verify credential expiry.</p></div>
      <button className="btn2" type="button" disabled={health.isFetching} onClick={() => health.refetch()}>Refresh integration health</button></header>
    {health.isPending && <p role="status">Loading integration health…</p>}
    {health.isError && <p role="alert">Integration health could not be loaded. {data ? "The information below may be out of date." : "Use Refresh to try again."}</p>}
    {data && <>
      <div className="row-gap" aria-label="Integration summary"><span className="chip g">{data.items.filter(item => item.enabled && item.state === "ok").length} healthy</span><span className="chip a">{data.items.filter(item => item.enabled && attention(item.state)).length} need attention</span><span className="chip n">{data.items.filter(item => !item.enabled).length} off</span><span className="muted small">Updated {when(data.checked_at)}</span></div>
      {data.worker_state !== "running" && <p role="status" className="error">{data.worker_state === "never_started" ? "The background worker has not checked in yet." : "The background worker is not checking in."} Review the services below before retrying integrations.</p>}
      <label className="row-gap">Show integrations <select aria-label="Show integrations" value={filter} onChange={event => setFilter(event.target.value)}><option value="enabled">Enabled</option><option value="attention">Needs attention</option><option value="all">All, including off</option></select></label>
      <div className="table-wrap"><table className="grid"><thead><tr><th>Integration</th><th>Health and next step</th><th>Saved evidence</th><th>Review</th></tr></thead><tbody>
        {items.map(item => <tr key={item.key}><td><strong>{item.label}</strong><div className="muted small">{item.evidence}</div></td><td><span className={`chip ${STATES[item.state][0]}`}>{STATES[item.state][1]}</span><p>{item.summary}</p><div className="muted small">{item.next_step}</div></td><td className="small"><div>Last attempt: {item.last_attempt_at ? when(item.last_attempt_at) : "Not recorded"}</div><div>Last success: {item.last_success_at ? when(item.last_success_at) : "Not recorded"}</div><div>Next due: {item.next_due_at ? when(item.next_due_at) : "Not scheduled"}</div></td><td><Link to={item.settings_path}>Configure {item.label}</Link></td></tr>)}
        {!items.length && <tr><td colSpan={4}>{filter === "attention" ? "No enabled integrations need attention in the saved data." : "No enabled integrations. Local helpdesk operation remains available."}</td></tr>}
      </tbody></table></div>
      <div className="integration-relays"><h3>Relays</h3><p className="muted small">Active relays are late after three minutes without a check-in. Disabled relays are excluded. A check-in confirms the relay is polling, not that every target is reachable.</p>
        {data.relays.length ? <ul>{data.relays.map(relay => <li key={relay.id}><span className={`chip ${STATES[relay.state][0]}`}>{relay.state === "ok" ? "Checking in" : relay.last_seen_at ? "Not checking in" : "Awaiting first check-in"}</span> <strong>{relay.name}</strong> <span className="muted small">{relay.last_seen_at ? when(relay.last_seen_at) : "Never seen"}</span></li>)}</ul> : <p>No active relays configured.</p>}
        {!!problemRelays.length && <p>{problemRelays.length} relay(s) need attention. Pending script or directory tasks may be waiting.</p>}
        <Link to="/settings/runners">Review relays</Link>
      </div>
      <p className="muted small">A successful pass is historical evidence, not a live availability guarantee. Teams here means queue summaries; technicians’ chat/Copilot connections are managed individually. Use the integration’s existing test action when investigating credentials.</p>
    </>}
  </section>;
}
