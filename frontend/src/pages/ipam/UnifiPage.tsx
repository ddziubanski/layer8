// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { when } from "../../format";

const KIND: Record<string, string> = { gateway: "Gateway", switch: "Switch", ap: "Access point", other: "Device" };

/** IP addresses > UniFi: equipment with its state, and the clients UniFi last saw (cached, read only). */
export function UnifiPage() {
  const [q, setQ] = useState("");
  const devices = useQuery({ queryKey: ["unifi-devices"], queryFn: () => api.unifi.devices() });
  const clients = useQuery({ queryKey: ["unifi-clients", q], queryFn: () => api.unifi.clients({ q }) });
  const offline = (devices.data ?? []).filter((d) => d.state === "offline").length;
  return (
    <div className="stack">
      <section className="card stack" aria-label="UniFi equipment">
        <div className="row-gap">
          <h2 className="spacer" style={{ margin: 0 }}>Equipment</h2>
          <span className={offline ? "chip r" : "chip g"}>{offline ? `${offline} offline` : "All online"}</span>
        </div>
        {devices.error && <p className="error">{devices.error.message}</p>}
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr><th>Name</th><th>Kind</th><th>Model</th><th>Site</th><th>Address</th><th>MAC</th><th>Firmware</th><th>State</th></tr>
            </thead>
            <tbody>
              {devices.data?.map((d) => (
                <tr key={`${d.source_id}:${d.device_key}`}>
                  <td>{d.name}</td>
                  <td>{KIND[d.kind] ?? d.kind}</td>
                  <td>{d.model}</td>
                  <td>{d.source_name}</td>
                  <td className="mono">{d.address}</td>
                  <td className="mono small">{d.mac}</td>
                  <td className="small">{d.firmware}</td>
                  <td><span className={`chip ${d.state === "online" ? "g" : d.state === "offline" ? "r" : "n"}`}>{d.state}</span></td>
                </tr>
              ))}
              {devices.data?.length === 0 && <tr><td colSpan={8} className="muted small">Nothing read yet. Settings &gt; UniFi has the connection and a Read now button.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card stack" aria-label="UniFi clients">
        <div className="row-gap">
          <h2 className="spacer" style={{ margin: 0 }}>Clients</h2>
          <input type="search" aria-label="Search UniFi clients" placeholder="Name, IP, MAC, access point, SSID or network" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <p className="muted small" style={{ margin: 0 }}>What UniFi last reported; documented IP records are not changed. Matching records show this beside them.</p>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr><th>Name</th><th>IP</th><th>MAC</th><th>Connected to</th><th>Network / SSID</th><th>Site</th><th>Read</th></tr>
            </thead>
            <tbody>
              {clients.data?.map((c) => (
                <tr key={`${c.source_id}:${c.mac}`} className={c.stale ? "muted" : undefined}>
                  <td>{c.hostname || "(no name)"}</td>
                  <td className="mono">{c.address}</td>
                  <td className="mono small">{c.mac}</td>
                  <td>{[c.uplink_name, c.port && `port ${c.port}`, c.connection].filter(Boolean).join(" · ")}</td>
                  <td>{[c.network, c.ssid].filter(Boolean).join(" · ")}</td>
                  <td>{c.source_name}</td>
                  <td className="small">{when(c.observed_at)}{c.stale && " (stale)"}</td>
                </tr>
              ))}
              {clients.data?.length === 0 && <tr><td colSpan={7} className="muted small">No clients match.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
