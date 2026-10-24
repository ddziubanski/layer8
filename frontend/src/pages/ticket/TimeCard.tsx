// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";

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
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="lbl" style={{ margin: 0 }}>Time spent{time.data ? `: ${minutesText(time.data.total_minutes)}` : ""}</div>
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
      {(log.error || remove.error || time.error) && <p className="error small" role="alert">{(log.error || remove.error || time.error)?.message}</p>}
    </div>
  );
}
