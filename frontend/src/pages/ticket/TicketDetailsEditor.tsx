// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Ticket } from "../../api/client";
import { TicketSaveHere, useTicketSaveTask } from "./TicketSave";

export function TicketDetailsEditor({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const [subject, setSubject] = useState<string | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const client = useQueryClient();
  const update = useMutation({ mutationFn: () => api.updateTicket(ticket.id, {
    ...(subject !== null && subject !== ticket.subject ? { subject: subject.trim() } : {}),
    ...(description !== null && description !== ticket.description ? { description } : {}),
  }), onSuccess: saved => { client.setQueryData(["ticket", ticket.id], saved); setSubject(null); setDescription(null); }, onSettled: onChanged });
  const dirty = (subject !== null && subject !== ticket.subject) || (description !== null && description !== ticket.description);
  const busy = useTicketSaveTask("details", { dirty, busy: update.isPending, label: "Ticket details", error: update.error?.message,
    validate: () => { if (!(subject ?? ticket.subject).trim()) throw new Error("Enter a ticket subject."); }, save: () => update.mutateAsync(),
    discard: () => { setSubject(null); setDescription(null); update.reset(); } });
  return <section className="card stack" aria-label="Ticket details" data-unsaved-work={dirty}>
    <div className="lbl">Ticket details</div>
    <label className="field">Subject<input maxLength={300} value={subject ?? ticket.subject} disabled={busy} onChange={e => setSubject(e.target.value)} /></label>
    <label className="field">Description<textarea rows={4} value={description ?? ticket.description ?? ""} disabled={busy} onChange={e => setDescription(e.target.value)} /></label>
    <TicketSaveHere show={dirty} own="Ticket details" />
    {update.error && <p className="error" role="alert">{update.error.message}</p>}
  </section>;
}
