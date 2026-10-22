import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { api, type Priority, type TicketSource, type User } from "../api/client";
import { UserPicker } from "../components/UserPicker";
import { PRIORITY_LABEL, SOURCE_LABEL } from "../format";

const SOURCES: TicketSource[] = ["agent", "email", "portal", "api"];
const PRIORITIES: Priority[] = ["low", "medium", "high", "critical"];

export function NewTicketDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const first = useRef<HTMLInputElement>(null);
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const departments = useQuery({ queryKey: ["field-options", "department"], queryFn: () => api.fieldOptions("department") });
  const [requester, setRequester] = useState<Pick<User, "id" | "display_name" | "email" | "phone" | "department"> | null>(null);
  const [form, setForm] = useState({
    subject: "",
    description: "",
    category: "",
    priority: "medium" as Priority,
    source: "agent" as TicketSource,
    queue_id: "",
    contact_phone: "",
    department_id: "",
  });

  // Picking someone fills their phone and department from AD (still editable).
  function pickRequester(user: typeof requester) {
    setRequester(user);
    const dept = user?.department ? departments.data?.find((d) => d.label.toLowerCase() === user.department!.toLowerCase()) : undefined;
    setForm((f) => ({ ...f, contact_phone: user?.phone ?? "", department_id: dept ? String(dept.id) : "" }));
  }
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const create = useMutation({
    mutationFn: () =>
      api.createTicket({
        subject: form.subject,
        description: form.description,
        category: form.category || null,
        priority: form.priority,
        // Nobody picked means "for myself".
        requester_id: requester?.id ?? null,
        // Blank: the requester's, from AD.
        contact_phone: form.contact_phone || null,
        department_id: form.department_id ? Number(form.department_id) : null,
        source: form.source,
        // Blank lets routing decide.
        queue_id: form.queue_id ? Number(form.queue_id) : null,
      }),
    onSuccess: (t) => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
      queryClient.invalidateQueries({ queryKey: ["views"] });
      navigate(`/tickets/${t.id}`);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div className="overlay" onClick={onClose}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="new-ticket" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h2 id="new-ticket">New ticket</h2>
        <label className="field">
          Subject
          <input ref={first} required maxLength={300} value={form.subject} onChange={set("subject")} />
        </label>
        <label className="field">
          Details
          <textarea rows={5} value={form.description} onChange={set("description")} />
        </label>
        <UserPicker label="Requester" value={requester} onChange={(u) => pickRequester(u as typeof requester)} placeholder="Type a name or email (blank: yourself)" />
        <div className="row-gap">
          <label className="field spacer">
            Contact phone
            <input type="tel" maxLength={64} value={form.contact_phone} onChange={set("contact_phone")} placeholder="From AD if blank" />
          </label>
          <label className="field spacer">
            Department
            <select value={form.department_id} onChange={set("department_id")}>
              <option value="">From AD / not set</option>
              {(departments.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="field">
          Queue
          <select value={form.queue_id} onChange={set("queue_id")}>
            <option value="">Let routing decide</option>
            {(queues.data ?? [])
              .filter((q) => q.is_active)
              .map((q) => (
                <option key={q.id} value={q.id}>
                  {q.name}
                </option>
              ))}
          </select>
        </label>
        <div className="row-gap">
          <label className="field spacer">
            Category
            <input maxLength={100} value={form.category} onChange={set("category")} />
          </label>
          <label className="field spacer">
            Priority
            <select value={form.priority} onChange={set("priority")}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </select>
          </label>
          <label className="field spacer">
            Received via
            <select value={form.source} onChange={set("source")}>
              {SOURCES.map((s) => (
                <option key={s} value={s}>
                  {SOURCE_LABEL[s]}
                </option>
              ))}
            </select>
          </label>
        </div>
        {create.error && (
          <p className="error" role="alert">
            {create.error.message}
          </p>
        )}
        <div className="row-gap">
          <span className="spacer" />
          <button className="btn2" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="btn" type="submit" disabled={create.isPending}>
            Create ticket
          </button>
        </div>
      </form>
    </div>
  );
}
