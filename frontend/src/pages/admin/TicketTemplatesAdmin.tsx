// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, type Me, type Priority, type TicketTemplate } from "../../api/client";
import { PRIORITY_LABEL } from "../../format";

const PRIORITIES: Priority[] = ["low", "medium", "high", "critical"];

/** Settings > Ticket templates: ready-made new tickets, offered in the New ticket form. */
export function TicketTemplatesAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["ticket-templates", "all"], queryFn: () => api.ticketTemplates.list(true) });
  const [editing, setEditing] = useState<TicketTemplate | "new" | null>(null);
  const remove = useMutation({ mutationFn: api.ticketTemplates.remove, onSettled: () => queryClient.invalidateQueries({ queryKey: ["ticket-templates"] }) });

  return (
    <>
      <div className="row-gap">
        <h1 className="spacer">Ticket templates</h1>
        <button className="btn" type="button" onClick={() => setEditing("new")}>New template</button>
      </div>
      <p className="muted">
        Ready-made new tickets: a subject, details, category, priority and, if you like, a queue and a checklist to suggest. Technicians pick one in
        the New ticket form, see what it fills in, and change anything before creating. A template never holds a requester or an assignee.
      </p>
      {templates.error && <p className="error" role="alert">{templates.error.message}</p>}
      {remove.error && <p className="error" role="alert">{remove.error.message}</p>}
      {editing && <TemplateForm key={editing === "new" ? "new" : editing.id} template={editing === "new" ? null : editing} me={me} onDone={() => setEditing(null)} />}
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr><th>Name</th><th>Subject</th><th>Queue</th><th>Priority</th><th>Active</th><th /></tr>
          </thead>
          <tbody>
            {templates.data?.map((t) => (
              <tr key={t.id}>
                <td><button className="linkish" type="button" onClick={() => setEditing(t)}>{t.name}</button></td>
                <td>{t.subject}</td>
                <td>{t.queue_name ?? <span className="muted">Routing decides</span>}</td>
                <td>{t.priority ? PRIORITY_LABEL[t.priority] : <span className="muted">Default</span>}</td>
                <td>{t.is_active ? "Yes" : "No"}</td>
                <td><button className="linkish small" type="button" disabled={remove.isPending} onClick={() => remove.mutate(t.id)} aria-label={`Delete ${t.name}`}>Delete</button></td>
              </tr>
            ))}
            {templates.data?.length === 0 && <tr><td colSpan={6} className="muted">No ticket templates yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TemplateForm({ template, onDone }: { template: TicketTemplate | null; me: Me; onDone: () => void }) {
  const queryClient = useQueryClient();
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const checklists = useQuery({ queryKey: ["checklist-templates"], queryFn: () => api.checklists.templates() });
  const [f, setF] = useState({
    name: template?.name ?? "", subject: template?.subject ?? "", description: template?.description ?? "", category: template?.category ?? "",
    priority: template?.priority ?? "", queue_id: template?.queue_id ? String(template.queue_id) : "",
    checklist_id: template?.checklist_id ? String(template.checklist_id) : "", is_active: template?.is_active ?? true,
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name, subject: f.subject, description: f.description, category: f.category || null, priority: (f.priority || null) as Priority | null,
        queue_id: f.queue_id ? Number(f.queue_id) : null, checklist_id: f.checklist_id ? Number(f.checklist_id) : null, is_active: f.is_active,
      };
      return template ? api.ticketTemplates.update(template.id, body) : api.ticketTemplates.create(body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ticket-templates"] });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="card stack" aria-label={template ? `Edit ${template.name}` : "New ticket template"} onSubmit={submit}>
      <label className="field">Name<input required maxLength={120} value={f.name} onChange={set("name")} /></label>
      <label className="field">Subject<input maxLength={300} value={f.subject} onChange={set("subject")} /></label>
      <label className="field">Details<textarea rows={5} maxLength={20000} value={f.description} onChange={set("description")} /></label>
      <div className="row-gap">
        <label className="field spacer">Category<input maxLength={100} value={f.category} onChange={set("category")} /></label>
        <label className="field spacer">Priority
          <select value={f.priority} onChange={set("priority")}>
            <option value="">Default</option>
            {PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
          </select>
        </label>
        <label className="field spacer">Queue
          <select value={f.queue_id} onChange={set("queue_id")}>
            <option value="">Routing decides</option>
            {queues.data?.filter((q) => q.is_active).map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
          </select>
        </label>
      </div>
      <label className="field">Suggest a checklist (optional; never started automatically)
        <select value={f.checklist_id} onChange={set("checklist_id")}>
          <option value="">None</option>
          {checklists.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </label>
      <label className="inline"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />Offer it in the New ticket form</label>
      {save.error && <p className="error" role="alert">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending || !f.name.trim()}>Save</button>
        <button className="btn2" type="button" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}
