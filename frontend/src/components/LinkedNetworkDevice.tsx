// SPDX-License-Identifier: AGPL-3.0-or-later
import { Link } from "react-router";
import type { TicketNetworkLink } from "../api/client";
import { DeviceSource } from "./DeviceSource";
import { when } from "../format";

export function LinkedNetworkDevice({ link }: { link: TicketNetworkLink }) {
  const target = link.current ?? link.target;
  return <span className="stack" style={{ gap: 3, minWidth: 0, overflowWrap: "anywhere" }}>
    <span>{target.kind === "ip_address" ? <Link to={`/ipam/addresses?q=${encodeURIComponent(target.address)}`}>{target.name}</Link> : <strong>{target.name}</strong>} <DeviceSource label={target.source_label} /></span>
    <span className="muted small">{[target.address, target.mac, target.source_name, target.details].filter(Boolean).join(" · ")}</span>
    {target.observed_at && <span className="muted small">{target.stale ? "Stale observation · " : ""}Observed {when(target.observed_at)}</span>}
    {!link.current && <span className="muted small">Saved when linked; no longer in the current scan or source is disabled.</span>}
    {link.current && link.current.address !== link.target.address && <span className="muted small">IP when linked: {link.target.address || "unknown"}</span>}
  </span>;
}
