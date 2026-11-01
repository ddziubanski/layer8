// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { elapsed, useNow, useTimerMutations } from "../../components/TicketTimers";
import { ME_KEY } from "../../queryClient";

export function minutesText(m: number): string {
  const h = Math.floor(m / 60);
  return h ? `${h} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`;
}

/** Time spent on the ticket: the total, each entry, and logging more. */
export function TimeCard({ ticketId }: { ticketId: number }) {
  const queryClient = useQueryClient();
  const key = ["ticket-time", ticketId];
  const time = useQuery({ queryKey: key, queryFn: () => api.time.forTicket(ticketId) });
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const log = useMutation({
    mutationFn: () => api.time.log(ticketId, { minutes: Number(minutes), note }),
    onSuccess: () => { setMinutes(""); setNote(""); void refresh(); },
  });
  const remove = useMutation({ mutationFn: (id: number) => api.time.remove(ticketId, id), onSettled: refresh });
  const valid = Number(minutes) >= 1 && Number(minutes) <= 1440;
  const myId = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false }).data?.id ?? 0;
  const mine = (time.data?.timers ?? []).some((t) => t.user_id === myId);
  const now = useNow(!!time.data?.timers?.length);
  const timer = useTimerMutations(ticketId);
  return (
    <div className="card stack" style={{ gap: 8 }}>
      <div className="lbl" style={{ margin: 0 }}>Time spent{time.data ? `: ${minutesText(time.data.total_minutes)}` : ""}</div>
      {(time.data?.timers ?? []).map((t) => (
        <div key={t.user_id} className="row-gap small timer-row">
          <span className="timer-pulse" aria-hidden="true" />
          <span className="spacer">{t.user_id === myId ? "Your timer" : `${t.user_name}'s timer`} · running {elapsed(t.started_at, now)}</span>
          {t.user_id === myId && <button className="btn2" type="button" disabled={timer.stop.isPending} onClick={() => timer.stop.mutate(note, { onSuccess: () => setNote("") })}>Stop and log{note.trim() ? " with note" : ""}</button>}
        </div>
      ))}
      {!mine && myId > 0 && <div><button className="btn2" type="button" disabled={timer.start.isPending} onClick={() => timer.start.mutate()}>Start timer</button></div>}
      {time.data?.entries.map((e) => (
        <div key={e.id} className="row-gap small">
          <span className="spacer">
            <strong>{minutesText(e.minutes)}</strong> · {e.user_name ?? "someone"} · {e.worked_on}{e.note ? ` · ${e.note}` : ""}
          </span>
          {e.may_delete && <button className="linkish" type="button" aria-label={`Remove ${minutesText(e.minutes)} by ${e.user_name}`} onClick={() => remove.mutate(e.id)}>Remove</button>}
        </div>
      ))}
      <form className="row-gap" onSubmit={(ev) => (ev.preventDefault(), valid && log.mutate())}>
        <label className="field" style={{ width: 110 }}>
          Minutes
          <input type="number" min={1} max={1440} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </label>
        <label className="field spacer">
          Note (optional)
          <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button className="btn2" type="submit" disabled={!valid || log.isPending}>Log time</button>
      </form>
      {(log.error || remove.error || time.error || timer.start.error || timer.stop.error) && <p className="error small" role="alert">{(log.error || remove.error || time.error || timer.start.error || timer.stop.error)?.message}</p>}
    </div>
  );
}
