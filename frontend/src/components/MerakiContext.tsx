import type { components } from "../api/schema";
import { when } from "../format";

type Observation = components["schemas"]["MerakiClientOut"];

/** Cached observations are visibly separate from documented addresses and SNMP evidence. */
export function MerakiContext({ observation: o }: { observation: Observation }) {
  return <div className="meraki-context small" aria-label="Meraki observation">
    <strong>Meraki · {o.network_name}</strong>{" · "}<span>{o.stale ? "Stale observation" : o.status || "Status unknown"}</span>
    <div>{[o.switch_name || o.switch_serial, o.port && `Port ${o.port}`, o.vlan && `VLAN ${o.vlan}`, o.ssid && `SSID ${o.ssid}`, o.connection].filter(Boolean).join(" · ")}</div>
    <div className="muted">Observed IP {o.address || "unknown"} · Last seen {o.last_seen_at ? when(o.last_seen_at) : "unknown"} · Read {when(o.observed_at)}</div>
  </div>;
}
