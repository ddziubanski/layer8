// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, type Ticket } from "../../api/client";
import { PriorityChip, StatusChip } from "../../components/Chips";
import { UpdateEveryone } from "./UpdateEveryone";

/** The ticket's child tickets (tasks for other technicians), and adding one. */
export function ChildTickets({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const key = ["ticket", ticket.id, "children"];
  const children = useQuery({ queryKey: key, queryFn: () => api.children(ticket.id) });
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const [adding, setAdding] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [queueId, setQueueId] = useState(String(ticket.queue.id));
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState<Ticket["priority"]>(ticket.priority);
  const owners = useQuery({ queryKey: ["assignable", queueId], queryFn: () => api.assignable(Number(queueId)), enabled: adding });
  const create = useMutation({
    mutationFn: async () => {
      const child = await api.createTicket({ subject, description, parent_id: ticket.id, queue_id: Number(queueId), priority, category: ticket.category ?? null });
      if (assignee) await api.assign(child.id, { assignee_id: Number(assignee), unassign: false });
      return child;
    },
    onSuccess: () => {
      setAdding(false);
      setSubject("");
      setDescription("");
      setAssignee("");
      queryClient.invalidateQueries({ queryKey: key });
      onChanged();
    },
  });
  const list = children.data ?? [];

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="Child tickets">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Child tickets {list.length > 0 && <span className="muted">({list.filter((c) => !["resolved", "closed"].includes(c.status)).length} open)</span>}
        </div>
        {list.some((c) => !["resolved", "closed"].includes(c.status)) && !updating && (
          <button className="btn2" type="button" onClick={() => setUpdating(true)}>
            Update everyone affected
          </button>
        )}
        {!adding && (
          <button className="btn2" type="button" onClick={() => setAdding(true)}>
            Add a child ticket
          </button>
        )}
      </div>
      {updating && <UpdateEveryone ticket={ticket} onDone={() => { setUpdating(false); void queryClient.invalidateQueries({ queryKey: key }); onChanged(); }} />}
      {list.length === 0 && !adding && <p className="muted small" style={{ margin: 0 }}>None. A child ticket is a piece of this work another technician can take on.</p>}
      {list.map((c) => (
        <div key={c.id} className="row-gap child-row">
          <Link to={`/tickets/${c.id}`} className="spacer">
            {c.label} {c.subject}
          </Link>
          <span className="muted small">
            {c.assignee?.display_name ?? "Unassigned"} · {c.queue.name}
          </span>
          <PriorityChip priority={c.priority} />
          <StatusChip status={c.status} />
        </div>
      ))}
      {adding && (
        <form
          className="stack"
          style={{ gap: 8 }}
          aria-label="New child ticket"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label className="field">
            Subject
            <input required autoFocus maxLength={300} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Set up the laptop" />
          </label>
          <label className="field">
            Details <span className="muted small">(optional)</span>
            <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="row-gap">
            <label className="field">
              Queue
              <select value={queueId} onChange={(e) => (setQueueId(e.target.value), setAssignee(""))}>
                {queues.data
                  ?.filter((q) => q.is_active)
                  .map((q) => (
                    <option key={q.id} value={q.id}>
                      {q.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              Assign to
              <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="">Nobody yet</option>
                {owners.data?.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Priority
              <select value={priority} onChange={(e) => setPriority(e.target.value as Ticket["priority"])}>
                {["critical", "high", "medium", "low"].map((p) => (
                  <option key={p} value={p}>
                    {p[0].toUpperCase() + p.slice(1)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <span className="muted small">Same requester as this ticket ({ticket.requester.display_name}).</span>
          {create.error && (
            <p className="error" role="alert" style={{ margin: 0 }}>
              {create.error.message}
            </p>
          )}
          <div className="row-gap">
            <button className="btn" type="submit" disabled={create.isPending}>
              Create child ticket
            </button>
            <button className="btn2" type="button" onClick={() => setAdding(false)}>
              Cancel
            </button>
            {create.data && (
              <button className="linkish small" type="button" onClick={() => navigate(`/tickets/${create.data!.id}`)}>
                Open #{create.data.id}
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
