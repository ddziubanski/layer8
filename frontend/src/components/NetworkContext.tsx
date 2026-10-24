// SPDX-License-Identifier: AGPL-3.0-or-later
import type { components } from "../api/schema";
import { when } from "../format";

type Observation = components["schemas"]["NetworkObservationOut"];

/** What a vendor integration (UniFi, Juniper) last saw for this MAC: cached, beside the documented record. */
export function NetworkContext({ observations }: { observations: Observation[] }) {
  return (
    <>
      {observations.map((o) => (
        <div key={`${o.provider}:${o.source_id}`} className="meraki-context small" aria-label={`${o.provider_label} observation`}>
          <strong>
            {o.provider_label} · {o.source_name}
          </strong>
          {o.stale && " · Stale observation"}
          <div>{[o.uplink_name, o.port && `Port ${o.port}`, o.network, o.ssid && `SSID ${o.ssid}`, o.connection].filter(Boolean).join(" · ")}</div>
          <div className="muted">
            Observed IP {o.address || "unknown"}
            {o.hostname && ` · ${o.hostname}`} · Read {when(o.observed_at)}
          </div>
        </div>
      ))}
    </>
  );
}
