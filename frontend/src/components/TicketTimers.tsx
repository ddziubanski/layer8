// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";

export const TIMERS_KEY = ["timers"];

/** 0:42, 12:05, 1:02:05: minutes and seconds, with hours once there are any. */
export function elapsed(startedAt: string, now: number): string {
  const s = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

/** The current time, ticking once a second while something is being timed. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

export function useTimerMutations(ticketId: number) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: TIMERS_KEY });
    void client.invalidateQueries({ queryKey: ["ticket-time", ticketId] });
    void client.invalidateQueries({ queryKey: ["ticket", ticketId, "events"] });
  };
  const start = useMutation({ mutationFn: () => api.time.start(ticketId), onSuccess: refresh });
  const stop = useMutation({ mutationFn: (note: string) => api.time.stop(ticketId, note), onSuccess: refresh });
  return { start, stop };
}

/** "Active ticket timers": the technician's running clocks, on every console page. */
export function ActiveTimers() {
  const timers = useQuery({ queryKey: TIMERS_KEY, queryFn: api.time.mine, refetchInterval: 30_000, refetchOnWindowFocus: true });
  const list = timers.data ?? [];
  const now = useNow(list.length > 0);
  if (!list.length) return null;
  return (
    <div className="active-timers" role="region" aria-label="Active ticket timers">
      <span className="active-timers-title"><span className="timer-pulse" aria-hidden="true" />Active ticket timer{list.length === 1 ? "" : "s"}</span>
      {list.map((t) => <TimerChip key={t.ticket_id} ticketId={t.ticket_id} label={t.ticket_label} subject={t.subject} time={elapsed(t.started_at, now)} />)}
    </div>
  );
}

function TimerChip({ ticketId, label, subject, time }: { ticketId: number; label: string; subject: string; time: string }) {
  const { stop } = useTimerMutations(ticketId);
  return (
    <span className="timer-chip">
      <Link to={`/tickets/${ticketId}`} title={subject}>{label} <span className="timer-subject">{subject}</span></Link>
      <span className="timer-time" aria-label={`running ${time}`}>{time}</span>
      <button type="button" className="linkish small" disabled={stop.isPending} onClick={() => stop.mutate("")} aria-label={`Stop the timer on ${label}`}>Stop</button>
    </span>
  );
}

/** Start or stop my timer on this ticket (the ticket's action bar). */
export function TimerButton({ ticketId, myId, closed }: { ticketId: number; myId: number; closed: boolean }) {
  const time = useQuery({ queryKey: ["ticket-time", ticketId], queryFn: () => api.time.forTicket(ticketId) });
  const mine = (time.data?.timers ?? []).find((t) => t.user_id === myId);
  const now = useNow(!!mine);
  const { start, stop } = useTimerMutations(ticketId);
  const error = start.error ?? stop.error;
  if (closed && !mine) return null;
  return (
    <>
      {mine
        ? <button className="btn2 timer-running" type="button" disabled={stop.isPending} onClick={() => stop.mutate("")} title="Stop and log the time">
            <span className="timer-pulse" aria-hidden="true" />Stop timer {elapsed(mine.started_at, now)}
          </button>
        : <button className="btn2" type="button" disabled={start.isPending || time.isPending} onClick={() => start.mutate()}>Start timer</button>}
      {error && <span className="error small" role="alert">{error.message}</span>}
    </>
  );
}

/** The ticket's total time for "At a glance", and whether anyone is timing it right now. */
export function TimeSpentTotal({ ticketId }: { ticketId: number }) {
  const time = useQuery({ queryKey: ["ticket-time", ticketId], queryFn: () => api.time.forTicket(ticketId) });
  if (!time.data) return <span className="muted">…</span>;
  const m = time.data.total_minutes;
  const text = m === 0 ? "None logged" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
  return <>{text}{(time.data.timers ?? []).length > 0 && <span className="timer-inline"><span className="timer-pulse" aria-hidden="true" />timing now</span>}</>;
}
