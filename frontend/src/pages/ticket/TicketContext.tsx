// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "../../api/client";
import { when } from "../../format";

const ROLE_LABEL = {
  computer: "Computer",
  linked: "Linked device",
  requester: "Requester's device",
} as const;

/** Cached context around the ticket: requester AD account, device live state, and nearby outages, problems and changes. */
export function TicketContext({ ticketId }: { ticketId: number }) {
  const { data } = useQuery({
    queryKey: ["ticket", ticketId, "context"],
    queryFn: () => api.ticketContext(ticketId),
    retry: false,
  });
  if (!data) return null;
  const hasAnything =
    data.account.length > 0 ||
    data.devices.length > 0 ||
    data.outages.length > 0 ||
    data.problems.length > 0 ||
    data.hidden_problems > 0 ||
    data.changes.length > 0;
  if (!hasAnything) return null;

  return (
    <section className="card stack ticket-context" aria-label="Around this ticket" style={{ gap: 10 }}>
      <div className="lbl" style={{ margin: 0 }}>Around this ticket</div>

      {data.account.length > 0 && (
        <div className="timeline-now" aria-label="Requester account">
          {data.account.map((a) => (
            <span key={a.kind} className={`timeline-fact tone-${a.tone}`}>
              <strong>Requester account:</strong> {a.label}
              {a.at && <span className="muted small">{when(a.at)}</span>}
            </span>
          ))}
        </div>
      )}

      {data.devices.map((d) => (
        <div key={d.id} className=" timeline-now" aria-label={`Device ${d.name}`}>
          <span className="timeline-fact">
            <span className="muted small">{ROLE_LABEL[d.role]}:</span>{" "}
            <Link to={`/devices/${d.id}`}><strong>{d.name}</strong></Link>
          </span>
          {d.online === true && <span className="timeline-fact tone-ok">Online</span>}
          {d.online === false && <span className="timeline-fact tone-bad">Offline</span>}
          {d.last_seen_at && <span className="timeline-fact">Last seen {when(d.last_seen_at)}</span>}
          {d.switch_port && <span className="timeline-fact">{d.switch_port}</span>}
          {(d.subnet || d.site) && (
            <span className="timeline-fact">{[d.subnet, d.site].filter(Boolean).join(" · ")}</span>
          )}
          {d.pending_reboot && <span className="timeline-fact tone-warn">Pending reboot</span>}
          {!!d.critical_patches && (
            <span className="timeline-fact tone-bad">
              {d.critical_patches} critical update{d.critical_patches === 1 ? "" : "s"} missing
            </span>
          )}
          {d.ad_disabled && <span className="timeline-fact tone-warn">Disabled in AD</span>}
        </div>
      ))}

      {data.outages.map((o, i) => (
        <div key={`${o.provider_label}-${o.name}-${i}`} className="row-gap small">
          <span className="chip r">Outage</span>
          <strong>{o.provider_label} {o.name}</strong>
          <span className="muted">
            ({o.scope}{o.down_since ? ` · down since ${when(o.down_since)}` : ""})
          </span>
          {o.ticket_id && (
            <Link to={`/tickets/${o.ticket_id}`}>{o.ticket_label ?? `#${o.ticket_id}`}</Link>
          )}
        </div>
      ))}

      {data.problems.map((p) => (
        <div key={p.id} className="stack" style={{ gap: 2 }}>
          <div className="row-gap small">
            <span className="chip a">Problem</span>
            <Link to={`/problems/${p.id}`}><strong>{p.label} {p.title}</strong></Link>
            <span className="muted">({p.scope} · {p.status.replace("_", " ")})</span>
          </div>
          {p.workaround && (
            <p className="small" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              <strong>Workaround:</strong> {p.workaround}
            </p>
          )}
        </div>
      ))}

      {!!data.hidden_problems && (
        <p className="muted small" style={{ margin: 0 }}>
          {data.hidden_problems} open problem{data.hidden_problems === 1 ? "" : "s"} nearby cannot be shown ({data.hidden_problems === 1 ? "it links" : "they link"} tickets you cannot view).
        </p>
      )}

      {data.changes.map((c) => (
        <div key={c.id} className="row-gap small">
          <span className="chip n">Change</span>
          <Link to={`/changes/${c.id}`}><strong>{c.label} {c.title}</strong></Link>
          <span className="muted">
            ({c.scope} · {c.status.replace("_", " ")}{c.at ? ` · ${when(c.at)}` : ""})
          </span>
        </div>
      ))}
    </section>
  );
}
