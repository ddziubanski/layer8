// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type Ticket, type TicketChecklist } from "../../api/client";
import { StatusChip } from "../../components/Chips";
import { UserPicker } from "../../components/UserPicker";
import { dateWord } from "../../checklists";

const DONE = ["resolved", "closed"];

/** A calendar date plus days (by the calendar, so a clock change doesn't shift it). */
function addDays(isoDate: string, days: number): Date {
  const d = new Date(isoDate + "T00:00");
  d.setDate(d.getDate() + days);
  return d;
}

/** Onboarding, offboarding and other checklists on the ticket: their steps (child tickets) and starting one. */
export function ChecklistsCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["ticket", ticket.id, "checklists"];
  const lists = useQuery({ queryKey: key, queryFn: () => api.checklists.forTicket(ticket.id) });
  const [starting, setStarting] = useState(false);
  const templates = useQuery({ queryKey: ["checklist-templates"], queryFn: () => api.checklists.templates(), enabled: starting });
  const [templateId, setTemplateId] = useState("");
  const [keyDate, setKeyDate] = useState("");
  const [person, setPerson] = useState<{ id: number; display_name: string; email: string } | null>(null);
  const [name, setName] = useState("");
  const template = templates.data?.find((t) => String(t.id) === templateId);
  const start = useMutation({
    mutationFn: () => api.checklists.start(ticket.id, { template_id: Number(templateId), key_date: keyDate || null, for_user_id: person?.id ?? null, for_name: name }),
    onSuccess: () => {
      setStarting(false);
      setTemplateId("");
      setKeyDate("");
      setPerson(null);
      setName("");
      queryClient.invalidateQueries({ queryKey: key });
      queryClient.invalidateQueries({ queryKey: ["ticket", ticket.id, "children"] });
      onChanged();
    },
  });
  const disables = template?.items.some((i) => i.action === "disable_ad_account");

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Checklists">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Checklists
        </div>
        {!starting && (
          <button className="btn2" type="button" onClick={() => setStarting(true)}>
            Start a checklist…
          </button>
        )}
      </div>
      {lists.data?.length === 0 && !starting && <span className="muted small">None. A checklist (new starter, leaver...) makes a child ticket per step, with due dates.</span>}
      {lists.data?.map((c) => {
        const done = c.items.filter((i) => i.child_ticket && DONE.includes(i.child_ticket.status)).length;
        return (
          <div key={c.id} className="stack" style={{ gap: 4 }} role="group" aria-label={`${c.name} for ${c.for_name}`}>
            <div className="row-gap">
              <strong className="spacer">
                {c.name} for {c.for_name}
              </strong>
              <span className="small muted">
                {c.key_date && `${dateWord(c.kind)} ${new Date(c.key_date + "T00:00").toLocaleDateString()} · `}
                {done} of {c.items.length} done
              </span>
            </div>
            <DateSetter ticketId={ticket.id} checklist={c} onDone={() => queryClient.invalidateQueries({ queryKey: key })} />
            <ul className="checklist-items small">
              {c.items.map((i) => (
                <li key={i.id} className={i.child_ticket && DONE.includes(i.child_ticket.status) ? "done" : undefined}>
                  {i.child_ticket ? <Link to={`/tickets/${i.child_ticket.id}`}>{i.title}</Link> : i.title}
                  {i.child_ticket && <StatusChip status={i.child_ticket.status} />}
                  <span className="muted">
                    {i.due_on && ` · due ${new Date(i.due_on + "T00:00").toLocaleDateString()}`}
                    {i.child_ticket?.assignee && ` · ${i.child_ticket.assignee.display_name}`}
                    {i.child_ticket && !i.child_ticket.assignee && ` · ${i.child_ticket.queue.name}`}
                    {i.action === "disable_ad_account" && " · disables the AD account that day"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {starting && (
        <form
          className="stack"
          style={{ gap: 8 }}
          aria-label="Start a checklist"
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate();
          }}
        >
          <div className="form-grid">
            <label className="field">
              Checklist
              <select required value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                <option value="">Choose…</option>
                {templates.data?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.items.length} steps)
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              {template ? dateWord(template.kind)[0].toUpperCase() + dateWord(template.kind).slice(1) : "Date"} <span className="muted small">(steps are due from it)</span>
              <input type="date" value={keyDate} required={disables} onChange={(e) => setKeyDate(e.target.value)} />
            </label>
            <UserPicker label="For (someone with an account)" value={person} onChange={setPerson} />
            <label className="field">
              Or their name <span className="muted small">(a new starter)</span>
              <input value={name} disabled={!!person} onChange={(e) => setName(e.target.value)} placeholder="e.g. John Smith" />
            </label>
          </div>
          {template?.description && <p className="small muted" style={{ margin: 0 }}>{template.description}</p>}
          {template && (
            <ul className="checklist-items small" aria-label="Steps it makes">
              {template.items.map((i, n) => (
                <li key={n}>
                  {i.title}
                  <span className="muted">
                    {keyDate && ` · due ${addDays(keyDate, i.offset_days ?? 0).toLocaleDateString()}`}
                    {!keyDate && i.offset_days ? ` · ${i.offset_days > 0 ? "+" : ""}${i.offset_days} day(s)` : ""}
                    {i.action === "disable_ad_account" && " · disables the AD account that day"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {disables && <p className="small muted" style={{ margin: 0 }}>The person must be from AD; the disable waits on the relay until that day and can be cancelled from the step&apos;s ticket.</p>}
          {start.error && <p className="error small">{start.error.message}</p>}
          <div className="row-gap">
            <button className="btn" type="submit" disabled={start.isPending || !templateId}>
              Start: make {template?.items.length ?? 0} child ticket(s)
            </button>
            <button className="btn2" type="button" onClick={() => setStarting(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

/** Set or move a checklist's start date / last day (e.g. one a rule started): the steps' due dates follow. */
function DateSetter({ ticketId, checklist, onDone }: { ticketId: number; checklist: TicketChecklist; onDone: () => void }) {
  const [open, setOpen] = useState(!checklist.key_date);
  const [value, setValue] = useState(checklist.key_date ?? "");
  const save = useMutation({ mutationFn: () => api.checklists.setDate(ticketId, checklist.id, value), onSuccess: () => (setOpen(false), onDone()) });
  const word = dateWord(checklist.kind);
  if (!open)
    return (
      <button className="linkish small" type="button" style={{ alignSelf: "flex-start" }} onClick={() => setOpen(true)}>
        Change the {word}
      </button>
    );
  return (
    <div className="row-gap small">
      <label className="inline">
        {checklist.key_date ? `New ${word}` : `No ${word} yet: set it`}
        <input type="date" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: "auto" }} />
      </label>
      <button className="btn2" type="button" disabled={!value || save.isPending} onClick={() => save.mutate()}>
        Set
      </button>
      {checklist.key_date && (
        <button className="linkish small" type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      )}
      <span className="muted">The steps&apos; due dates follow.</span>
      {save.error && <span className="error">{save.error.message}</span>}
    </div>
  );
}
