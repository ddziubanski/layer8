// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";
import { when } from "../format";

type Timeline = Awaited<ReturnType<typeof api.devices.timeline>>;
type Kind = Timeline["entries"][number]["kind"];

const KINDS: { id: Kind; label: string }[] = [
  { id: "ticket", label: "Tickets" },
  { id: "change", label: "Changes" },
  { id: "script", label: "Scripts" },
  { id: "remote", label: "Remote" },
  { id: "deployment", label: "Software" },
  { id: "network", label: "Network" },
  { id: "purchase", label: "Purchases" },
  { id: "lifecycle", label: "Lifecycle" },
];

function day(at: string) {
  return new Date(at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** Everything Layer 8 knows happened to this device, newest first, and what it is right now. */
export function DeviceTimeline({ deviceId }: { deviceId: number }) {
  const [off, setOff] = useState<Set<Kind>>(new Set());
  const timeline = useQuery({ queryKey: ["device", deviceId, "timeline"], queryFn: () => api.devices.timeline(deviceId), refetchInterval: 60_000 });
  const t = timeline.data;
  const shown = (t?.entries ?? []).filter((e) => !off.has(e.kind));
  const present = new Set((t?.entries ?? []).map((e) => e.kind));
  let lastDay = "";

  return (
    <section className="card stack device-timeline" aria-label="Timeline" style={{ gap: 12 }}>
      <div className="row-gap">
        <h2 className="spacer" style={{ margin: 0 }}>Timeline</h2>
        {timeline.isFetching && !timeline.isPending && <span className="muted small" role="status">Refreshing…</span>}
      </div>
      {timeline.error && <p className="error">{timeline.error.message}</p>}
      {timeline.isPending && <p className="muted">Gathering this device&apos;s history…</p>}
      {t && (t.now.length > 0 || t.upcoming.length > 0) && (
        <div className="timeline-now" aria-label="Right now and coming up">
          {t.now.map((n, i) => (
            <span key={`n${i}`} className={`timeline-fact tone-${n.tone}`} title={n.at ? `As of ${when(n.at)}` : undefined}>
              <span className="muted small">{n.label}</span> {n.value}{n.at && n.label === "Last boot" ? when(n.at) : ""}
            </span>
          ))}
          {t.upcoming.map((u, i) => (
            <span key={`u${i}`} className={`timeline-fact tone-${u.tone}`}>
              <span className="muted small">Coming up</span> {u.title} {day(u.at)}
            </span>
          ))}
        </div>
      )}
      {t && present.size > 1 && (
        <div className="row-gap" role="group" aria-label="Show in the timeline">
          {KINDS.filter((k) => present.has(k.id)).map((k) => (
            <button key={k.id} type="button" className="timeline-filter" aria-pressed={!off.has(k.id)}
              onClick={() => setOff((s) => { const n = new Set(s); if (n.has(k.id)) n.delete(k.id); else n.add(k.id); return n; })}>
              {k.label}
            </button>
          ))}
        </div>
      )}
      {t && shown.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing recorded for this device yet. Tickets, changes, scripts and scans that involve it will show here.</p>}
      {shown.length > 0 && (
        <ol className="timeline-list">
          {shown.map((e, i) => {
            const d = day(e.at);
            const head = d !== lastDay;
            lastDay = d;
            return (
              <li key={i} className={`timeline-entry tone-${e.tone}`}>
                {head && <div className="timeline-day">{d}</div>}
                <div className="timeline-row">
                  <span className="timeline-dot" aria-hidden="true" />
                  <div className="timeline-body">
                    <div>{e.link ? <Link to={e.link}>{e.title}</Link> : e.title}</div>
                    {e.detail && <div className="muted small">{e.detail}</div>}
                  </div>
                  <time className="muted small" dateTime={e.at}>{new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {t && t.hidden > 0 && (
        <p className="muted small" style={{ margin: 0 }}>
          {t.hidden} more {t.hidden === 1 ? "entry belongs" : "entries belong"} to tickets in queues you can&apos;t see.
        </p>
      )}
      {t?.truncated && <p className="muted small" style={{ margin: 0 }}>Showing the latest 300 entries.</p>}
    </section>
  );
}
