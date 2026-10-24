// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "../api/client";
import { when } from "../format";

/** A device's patch state from Action1 (read only). Compact: one line for ticket cards; nothing if Action1 doesn't know it. */
export function PatchStatus({ deviceId, compact = false }: { deviceId: number; compact?: boolean }) {
  const patch = useQuery({ queryKey: ["device-patch", deviceId], queryFn: () => api.action1.forDevice(deviceId) });
  const p = patch.data;
  if (!p) return compact ? null : <span className="muted small">Action1 doesn&apos;t know this device (or isn&apos;t set up).</span>;
  const problems = [
    p.missing_critical > 0 && `${p.missing_critical} critical update${p.missing_critical === 1 ? "" : "s"} missing`,
    p.reboot_required && `reboot pending${p.reboot_since ? ` since ${when(p.reboot_since)}` : ""}`,
  ].filter(Boolean);
  const tone = p.missing_critical > 0 ? "r" : p.reboot_required ? "a" : "g";
  const summary = problems.length ? problems.join(" · ") : p.missing_other ? `No critical updates missing (${p.missing_other} other)` : "Up to date";
  if (compact)
    return (
      <div className="small" aria-label="Patching">
        <span className={`chip ${tone}`} style={{ whiteSpace: "normal" }}>{summary}</span>
        {p.ticket_id && (
          <>
            {" "}
            <Link to={`/tickets/${p.ticket_id}`}>patching ticket</Link>
          </>
        )}
      </div>
    );
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div>
        <span className={`chip ${tone}`}>{summary}</span>
      </div>
      <dl className="facts small">
        <dt>Missing updates</dt>
        <dd>
          {p.missing_critical} critical, {p.missing_other} other
          {p.critical_since && <span className="muted"> · critical ones missing since {when(p.critical_since)}</span>}
        </dd>
        <dt>Vulnerabilities</dt>
        <dd>
          {p.vulns_critical} critical, {p.vulns_other} other
        </dd>
        <dt>Reboot pending</dt>
        <dd>{p.reboot_required ? `Yes${p.reboot_since ? `, since ${when(p.reboot_since)}` : ""}` : "No"}</dd>
        <dt>Agent</dt>
        <dd>
          {p.status || "unknown"} · last seen {p.last_seen_at ? when(p.last_seen_at) : "never"}
          {p.agent_version && <span className="muted"> · version {p.agent_version}</span>}
        </dd>
        {p.last_boot_at && (
          <>
            <dt>Last boot</dt>
            <dd>{when(p.last_boot_at)}</dd>
          </>
        )}
        {p.user && (
          <>
            <dt>Signed in</dt>
            <dd>{p.user}</dd>
          </>
        )}
        {p.ticket_id && (
          <>
            <dt>Ticket</dt>
            <dd>
              <Link to={`/tickets/${p.ticket_id}`}>Open the patching ticket</Link>
            </dd>
          </>
        )}
      </dl>
      <span className="muted small">
        From Action1{p.organization ? ` (${p.organization})` : ""}, read {when(p.synced_at)}. Patching is done in Action1.
      </span>
    </div>
  );
}
