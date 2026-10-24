// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";

import { ApiError, type WallboardData } from "../api/client";
import { Brand, useBranding } from "../branding";

type Ticket = WallboardData["unassigned"][number];

const REFRESH_MS = 30_000;

const TILES: { key: string; label: string; alarm?: boolean; warn?: boolean }[] = [
  { key: "open", label: "Open" },
  { key: "unassigned", label: "Unassigned", warn: true },
  { key: "sla_breached", label: "SLA breached", alarm: true },
  { key: "sla_at_risk", label: "SLA at risk", warn: true },
  { key: "waiting_on_user", label: "Waiting on user" },
  { key: "new_today", label: "New today" },
  { key: "resolved_today", label: "Resolved today" },
];

/** The board's data, fetched with the link's token (from the URL fragment, never sent as part of a URL). */
async function fetchBoard(token: string): Promise<WallboardData> {
  const res = await fetch("/api/public/wallboard", { headers: { Authorization: `Bearer ${token}` }, credentials: "omit", cache: "no-store" });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, (data && typeof data.detail === "string" && data.detail) || `HTTP ${res.status}`);
  return data as WallboardData;
}

/** "12 min", "3 h 05", "2 d 4 h": short enough to read across a room. */
export function span(ms: number): string {
  const m = Math.max(0, Math.floor(ms / 60_000));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, "0")}`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

function due(t: Ticket, now: number): string {
  if (!t.due_at) return "";
  const left = new Date(t.due_at).getTime() - now;
  return left <= 0 ? `${span(-left)} over` : `due in ${span(left)}`;
}

/** /wallboard#l8w_...: a read-only board for a big screen. No sign-in: the link is the key. */
export function WallboardPage() {
  const brand = useBranding();
  const token = window.location.hash.slice(1);
  const [data, setData] = useState<WallboardData | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!token) return;
    let stop = false;
    const load = () =>
      fetchBoard(token).then(
        (d) => !stop && (setData(d), setError(null)),
        (e) => !stop && setError(e),
      );
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => ((stop = true), window.clearInterval(timer));
  }, [token]);
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  useEffect(() => {
    document.documentElement.classList.add("wallboard-root");
    return () => document.documentElement.classList.remove("wallboard-root");
  }, []);

  const switchedOff = error instanceof ApiError && error.status === 401;
  if (!token || switchedOff) {
    return (
      <main className="wallboard wallboard-message">
        <Brand b={brand} />
        <h1>{token ? "This wallboard was switched off" : "No wallboard link"}</h1>
        <p>{token ? error?.message : "Open the full link from Settings > Wallboards, including the part after #."}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="wallboard wallboard-message">
        <Brand b={brand} />
        <p role="status">{error ? `Can't reach the service desk: ${error.message}. Trying again every 30 seconds.` : "Loading…"}</p>
      </main>
    );
  }

  const clock = new Date(now);
  const busy = data.queues.filter((q) => q.open > 0);
  const quiet = data.queues.length - busy.length;
  const stale = error !== null;
  return (
    <main className="wallboard" aria-label={data.name}>
      <header className="wb-head">
        <div className="wb-title">
          <Brand b={brand} />
          <span className="wb-name">{data.name}</span>
        </div>
        <div className={`wb-updated${stale ? " stale" : ""}`} role="status">
          {stale ? `Offline: last update ${new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : `Updated ${new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`}
        </div>
        <time className="wb-clock" dateTime={clock.toISOString()}>
          {clock.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          <span>{clock.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}</span>
        </time>
      </header>

      <section className="wb-alerts" aria-label="Alerts">
        {data.alerts.length === 0 ? (
          <div className="wb-alert ok">All clear: nothing past its SLA, nothing waiting for an owner.</div>
        ) : (
          data.alerts.map((a) => (
            <div key={a.text} className={`wb-alert ${a.level}`}>
              {a.text}
            </div>
          ))
        )}
      </section>

      <section className="wb-tiles" aria-label="Counts">
        {TILES.map((t) => {
          const n = data.totals[t.key] ?? 0;
          const tone = n > 0 && t.alarm ? " alarm" : n > 0 && t.warn ? " warn" : "";
          return (
            <div key={t.key} className={`wb-tile${tone}`}>
              <strong>{n}</strong>
              <span>{t.label}</span>
            </div>
          );
        })}
      </section>

      <div className="wb-lists">
        <section className="wb-panel" aria-label="Unassigned, oldest first">
          <h2>Unassigned <span>oldest first</span></h2>
          {data.unassigned.length === 0 ? (
            <p className="wb-empty">Every open ticket has an owner.</p>
          ) : (
            <ol>
              {data.unassigned.map((t) => {
                const age = now - new Date(t.created_at).getTime();
                const late = age > data.unassigned_alert_minutes * 60_000;
                return (
                  <li key={t.id} className={late ? "late" : undefined}>
                    <span className="wb-label">{t.label}</span>
                    <span className="wb-subject">{t.subject}</span>
                    <span className={`wb-prio p-${t.priority}`}>{t.priority}</span>
                    <span className="wb-meta">{t.queue} · {t.requester}</span>
                    <span className="wb-age">{span(age)}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
        <section className="wb-panel" aria-label="SLA, due soonest">
          <h2>SLA <span>breached and at risk, due soonest</span></h2>
          {data.sla.length === 0 ? (
            <p className="wb-empty">Nothing at risk.</p>
          ) : (
            <ol>
              {data.sla.map((t) => (
                <li key={t.id} className={t.breached ? "breached" : "risk"}>
                  <span className="wb-label">{t.label}</span>
                  <span className="wb-subject">{t.subject}</span>
                  <span className={`wb-prio p-${t.priority}`}>{t.priority}</span>
                  <span className="wb-meta">{t.assignee ?? "Unassigned"} · {t.requester}</span>
                  <span className="wb-age">{due(t, now)}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <div className="wb-lists">
        <section className="wb-panel" aria-label="Queues">
          <h2>Queues <span>open · unassigned · breached</span></h2>
          {/* Busy queues only, so the panel fits however many queues there are. */}
          {busy.length === 0 && <p className="wb-empty">Nothing open.</p>}
          <ul className="wb-bars">
            {busy.map((q) => {
              const top = Math.max(1, ...busy.map((x) => x.open));
              return (
                <li key={q.name}>
                  <span className="wb-bar-name">{q.name}</span>
                  <span className="wb-bar-track">
                    <span className="wb-bar" style={{ width: `${(100 * q.open) / top}%` }} />
                  </span>
                  <span className="wb-bar-nums">
                    {q.open} · <b className={q.unassigned ? "warn" : undefined}>{q.unassigned}</b> · <b className={q.breached ? "alarm" : undefined}>{q.breached}</b>
                  </span>
                </li>
              );
            })}
          </ul>
          {quiet > 0 && busy.length > 0 && <p className="wb-quiet">{quiet} more {quiet === 1 ? "queue" : "queues"} with nothing open</p>}
        </section>
        <section className="wb-panel" aria-label="Technicians">
          <h2>Technicians <span>open · needing attention</span></h2>
          {data.people.length === 0 ? (
            <p className="wb-empty">No tickets assigned.</p>
          ) : (
            <ul className="wb-bars">
              {data.people.map((p) => {
                const top = Math.max(1, ...data.people.map((x) => x.open));
                return (
                  <li key={p.name}>
                    <span className="wb-bar-name">{p.name}</span>
                    <span className="wb-bar-track">
                      <span className="wb-bar" style={{ width: `${(100 * p.open) / top}%` }} />
                    </span>
                    <span className="wb-bar-nums">
                      {p.open} · <b className={p.needs_attention ? "warn" : undefined}>{p.needs_attention}</b>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
