// SPDX-License-Identifier: AGPL-3.0-or-later
import type { PortalHours, PortalQueue } from "../api/client";

const STATUS: Record<string, string> = { available: "Available", busy: "Busy", away: "Away", offline: "Offline", unknown: "Status unknown" };

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function opensAt(iso: string): string {
  const at = new Date(iso);
  const today = new Date();
  const days = Math.round((new Date(at.toDateString()).getTime() - new Date(today.toDateString()).getTime()) / 86_400_000);
  const day = days === 0 ? "today" : days === 1 ? "tomorrow" : at.toLocaleDateString([], days < 7 ? { weekday: "long" } : { day: "numeric", month: "long" });
  return `${day} at ${clock(iso)}`;
}

/** "Open until 17:00" or "Closed: opens Monday at 08:00". */
export function OpenState({ hours }: { hours: PortalHours }) {
  if (hours.open_now) return <span className="open-state open">Open{hours.closes_at ? ` until ${clock(hours.closes_at)}` : ""}</span>;
  return (
    <span className="open-state closed">
      Closed{hours.closed_for ? ` (${hours.closed_for})` : ""}
      {hours.next_open_at ? `: opens ${opensAt(hours.next_open_at)}` : ""}
    </span>
  );
}

/** Teams status only: a dot and a word per person, never a link to chat or call. */
export function TeamStatus({ people }: { people: PortalQueue["technicians"] }) {
  if (!people) return null;
  if (people.length === 0) return <span className="muted small">No technicians listed for this queue.</span>;
  return (
    <ul className="team-status" aria-label="Technicians' Teams status">
      {people.map((p) => (
        <li key={p.name}>
          <span className={`presence-dot ${p.status}`} aria-hidden="true" />
          {p.name} <span className="muted">· {STATUS[p.status] ?? p.status}</span>
        </li>
      ))}
    </ul>
  );
}

/** Each queue's hours, whether it's open now and who is in: shown separately per queue. */
export function TeamsAndHours({ queues, desk }: { queues: PortalQueue[]; desk?: PortalHours }) {
  if (queues.length === 0 && !desk) return null;
  return (
    <section className="card stack hours-card" aria-label="Teams and hours">
      <div className="lbl" style={{ margin: 0 }}>Teams and hours</div>
      {queues.length === 0 && desk && (
        <div className="hours-row">
          <div className="row-gap">
            <strong className="spacer">Service desk</strong>
            <OpenState hours={desk} />
          </div>
          {desk.lines.length > 0 && <span className="muted small">{desk.lines.join(" · ")}</span>}
          {desk.message && <p className="small hours-message">{desk.message}</p>}
        </div>
      )}
      {queues.map((q) => (
        <div key={q.id} className="hours-row">
          <div className="row-gap">
            <strong className="spacer">{q.name}</strong>
            <OpenState hours={q.hours} />
          </div>
          {q.hours.lines.length > 0 && <span className="muted small">{q.hours.lines.join(" · ")}</span>}
          <TeamStatus people={q.technicians} />
        </div>
      ))}
    </section>
  );
}
