import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useNavigate } from "react-router";

import { api, type AssignIn, type Priority, type Ticket, type TicketStatus, type TicketUpdate } from "../../api/client";
import { SlaChip } from "../../components/Chips";
import { UserPicker } from "../../components/UserPicker";
import { PRIORITY_LABEL, STATUS_LABEL } from "../../format";
import { can } from "../../permissions";
import { ME_KEY } from "../../queryClient";

const STATUSES = Object.keys(STATUS_LABEL) as TicketStatus[];
const PRIORITIES = Object.keys(PRIORITY_LABEL) as Priority[];

type Props = { ticket: Ticket; onChanged: () => void };

export function AssignmentCard({ ticket, onChanged }: Props) {
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const assignable = useQuery({
    queryKey: ["assignable", ticket.queue.id],
    queryFn: () => api.assignable(ticket.queue.id),
  });
  const assign = useMutation({
    mutationFn: (body: AssignIn) => api.assign(ticket.id, body),
    onSuccess: () => setMoveTo(null),
    onSettled: onChanged,
  });
  // Picking another queue asks for an optional reason before moving.
  const [moveTo, setMoveTo] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  // The current assignee may no longer be an owner; still show who it is.
  const people = assignable.data ?? [];
  const assignee = ticket.assignee;
  const options = assignee && !people.some((p) => p.id === assignee.id) ? [assignee, ...people] : people;

  return (
    <div className="card">
      <div className="lbl">Assignment</div>
      <label className="field">
        Queue
        <select
          value={moveTo ?? ticket.queue.id}
          disabled={assign.isPending}
          onChange={(e) => {
            const id = Number(e.target.value);
            setMoveTo(id === ticket.queue.id ? null : id);
            setReason("");
          }}
        >
          {(queues.data ?? [ticket.queue])
            .filter((q) => !("is_active" in q) || q.is_active || q.id === ticket.queue.id)
            .map((q) => (
              <option key={q.id} value={q.id}>
                {q.name}
              </option>
            ))}
        </select>
      </label>
      {moveTo !== null ? (
        <form
          className="stack"
          style={{ gap: 8, margin: "8px 0 10px" }}
          onSubmit={(e) => {
            e.preventDefault();
            assign.mutate({ queue_id: moveTo, unassign: false, note: reason || null });
          }}
        >
          <label className="field">
            Reason for moving <span className="muted small">(optional, added to the internal note)</span>
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => setMoveTo(null)}>
              Cancel
            </button>
            <button className="btn" type="submit" disabled={assign.isPending}>
              Move ticket
            </button>
          </div>
        </form>
      ) : (
        <p className="muted small" style={{ margin: "4px 0 10px" }}>
          {ticket.matched_rule_name ? `Routed by rule: ${ticket.matched_rule_name}` : "No routing rule matched"}
        </p>
      )}
      <label className="field">
        Assignee
        <select
          value={assignee?.id ?? ""}
          disabled={assign.isPending}
          onChange={(e) =>
            assign.mutate(e.target.value ? { assignee_id: Number(e.target.value), unassign: false } : { unassign: true })
          }
        >
          <option value="">Unassigned</option>
          {options.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name}
            </option>
          ))}
        </select>
      </label>
      {assign.error && (
        <p className="error" role="alert">
          {assign.error.message}
        </p>
      )}
      <div style={{ marginTop: 10 }}>
        <SlaChip ticket={ticket} long />
      </div>
    </div>
  );
}

export function RequesterCard({ ticket, onChanged }: Props) {
  const [mode, setMode] = useState<null | "pick" | "email">(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState<string | null>(null);
  const departments = useQuery({ queryKey: ["field-options", "department"], queryFn: () => api.fieldOptions("department") });
  const change = useMutation({
    mutationFn: (who: Parameters<typeof api.changeRequester>[1]) => api.changeRequester(ticket.id, who),
    onSuccess: () => {
      setMode(null);
      setEmail("");
      setName("");
    },
    onSettled: onChanged,
  });
  const update = useMutation({
    mutationFn: (body: { contact_phone?: string | null; department_id?: number | null }) => api.updateTicket(ticket.id, body),
    onSuccess: () => setPhone(null),
    onSettled: onChanged,
  });
  // The ticket's department may have been retired from the list since; still show it.
  const deptOptions = departments.data ?? [];
  const dept = ticket.department;
  const deptChoices = dept && !deptOptions.some((d) => d.id === dept.id) ? [dept, ...deptOptions] : deptOptions;
  const error = change.error ?? update.error;

  return (
    <div className="card">
      <div className="lbl">Requester and contact</div>
      <div>{ticket.requester.display_name}</div>
      <div className="muted small">{ticket.requester.email}</div>
      {mode === "pick" && (
        <div className="stack" style={{ gap: 8, marginTop: 10 }}>
          <UserPicker label="Change requester to" value={null} autoFocus onChange={(u) => u && change.mutate({ user_id: u.id })} />
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => setMode(null)}>
              Cancel
            </button>
            <button className="linkish small" type="button" onClick={() => setMode("email")}>
              Not listed? Link by email
            </button>
          </div>
        </div>
      )}
      {mode === "email" && (
        <form
          className="stack"
          style={{ gap: 8, marginTop: 10 }}
          onSubmit={(e) => {
            e.preventDefault();
            change.mutate({ email, display_name: name || null });
          }}
        >
          <label className="field">
            Link to account (email)
            <input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="field">
            Name <span className="muted small">(only if it's a new account)</span>
            <input maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => setMode(null)}>
              Cancel
            </button>
            <button className="btn" type="submit" disabled={change.isPending}>
              Change requester
            </button>
          </div>
        </form>
      )}
      {mode === null && (
        <button className="linkish small" type="button" style={{ marginTop: 8 }} onClick={() => setMode("pick")}>
          Change requester
        </button>
      )}
      <div className="stack" style={{ gap: 8, marginTop: 12 }}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            update.mutate({ contact_phone: phone });
          }}
        >
          <label className="field">
            Contact phone
            <span className="row-gap" style={{ flexWrap: "nowrap" }}>
              <input type="tel" maxLength={64} value={phone ?? ticket.contact_phone ?? ""} onChange={(e) => setPhone(e.target.value)} />
              {phone !== null && phone !== (ticket.contact_phone ?? "") && (
                <button className="btn2" type="submit" disabled={update.isPending}>
                  Save
                </button>
              )}
            </span>
          </label>
        </form>
        <label className="field">
          Department
          <select
            value={dept?.id ?? ""}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ department_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Not set</option>
            {deptChoices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {Object.keys(ticket.imported_fields ?? {}).length > 0 && (
        <section aria-label="Imported details" style={{ marginTop: 12 }}>
          <div className="lbl">Imported details</div>
          <dl className="facts small" style={{ margin: 0 }}>
            {Object.entries(ticket.imported_fields ?? {}).map(([k, v]) => (
              <Fragment key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      )}
      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
    </div>
  );
}

const CLOSED: TicketStatus[] = ["resolved", "closed"];

export function StatusCard({ ticket, onChanged }: Props) {
  const resolutions = useQuery({ queryKey: ["field-options", "resolution_type"], queryFn: () => api.fieldOptions("resolution_type") });
  const reasons = useQuery({ queryKey: ["field-options", "closure_reason"], queryFn: () => api.fieldOptions("closure_reason") });
  // Resolving or closing asks how it was resolved and why it's closed first.
  const [closing, setClosing] = useState<TicketStatus | null>(null);
  const [resolution, setResolution] = useState("");
  const [reason, setReason] = useState("");
  const update = useMutation({
    mutationFn: (body: TicketUpdate) => api.updateTicket(ticket.id, body),
    onSuccess: () => setClosing(null),
    onSettled: onChanged,
  });
  const isClosed = CLOSED.includes(ticket.status);
  const asks = (resolutions.data?.length ?? 0) + (reasons.data?.length ?? 0) > 0;

  function setStatus(status: TicketStatus) {
    if (CLOSED.includes(status) && !isClosed && asks) {
      setClosing(status);
      setResolution(String(ticket.resolution_type?.id ?? ""));
      setReason(String(ticket.closure_reason?.id ?? ""));
    } else {
      update.mutate({ status });
    }
  }

  return (
    <div className="card">
      <div className="lbl">Status</div>
      <div className="stack" style={{ gap: 8 }}>
        <label className="field">
          Status
          <select value={closing ?? ticket.status} disabled={update.isPending} onChange={(e) => setStatus(e.target.value as TicketStatus)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        {closing && (
          <form
            className="stack resolve-form"
            style={{ gap: 8 }}
            aria-label="Resolution"
            onSubmit={(e) => {
              e.preventDefault();
              update.mutate({
                status: closing,
                resolution_type_id: resolution ? Number(resolution) : null,
                closure_reason_id: reason ? Number(reason) : null,
              });
            }}
          >
            {(resolutions.data?.length ?? 0) > 0 && (
              <label className="field">
                Resolution type
                <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
                  <option value="">Choose…</option>
                  {resolutions.data!.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {(reasons.data?.length ?? 0) > 0 && (
              <label className="field">
                Closure reason
                <select value={reason} onChange={(e) => setReason(e.target.value)}>
                  <option value="">Choose…</option>
                  {reasons.data!.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="row-gap">
              <button className="btn2" type="button" onClick={() => setClosing(null)}>
                Cancel
              </button>
              <button className="btn" type="submit" disabled={update.isPending}>
                {closing === "resolved" ? "Resolve" : "Close"} ticket
              </button>
            </div>
          </form>
        )}
        {isClosed && (ticket.resolution_type || ticket.closure_reason) && (
          <dl className="facts small" style={{ margin: 0 }}>
            {ticket.resolution_type && (
              <>
                <dt>Resolution</dt>
                <dd>{ticket.resolution_type.label}</dd>
              </>
            )}
            {ticket.closure_reason && (
              <>
                <dt>Closure reason</dt>
                <dd>{ticket.closure_reason.label}</dd>
              </>
            )}
          </dl>
        )}
        <label className="field">
          Priority
          <select value={ticket.priority} disabled={update.isPending} onChange={(e) => update.mutate({ priority: e.target.value as Priority })}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {update.error && (
        <p className="error" role="alert">
          {update.error.message}
        </p>
      )}
    </div>
  );
}

export function ActionsCard({ ticket, onChanged }: Props) {
  const [reason, setReason] = useState<string | null>(null);
  const [parentNo, setParentNo] = useState<string | null>(null);
  const [merging, setMerging] = useState<{ into: string; reason: string; note: string } | null>(null);
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me });
  const mayMerge = me.data ? can(me.data, "ticket_merge") : false;
  const mayDelete = me.data ? can(me.data, "ticket_delete") : false;
  const remove = useMutation({ mutationFn: () => api.deleteTicket(ticket.id), onSuccess: () => navigate("/") });
  const mergeReasons = useQuery({ queryKey: ["field-options", "merge_reason"], queryFn: () => api.fieldOptions("merge_reason"), enabled: merging !== null });
  const merge = useMutation({
    mutationFn: (m: { into: string; reason: string; note: string }) => api.merge(ticket.id, { into_id: Number(m.into.replace("#", "")), reason_id: Number(m.reason), note: m.note }),
    onSuccess: (target) => {
      setMerging(null);
      onChanged();
      navigate(`/tickets/${target.id}`);
    },
  });
  const reparent = useMutation({
    mutationFn: (parentId: number | null) => api.updateTicket(ticket.id, { parent_id: parentId }),
    onSuccess: () => setParentNo(null),
    onSettled: onChanged,
  });
  const escalate = useMutation({
    mutationFn: () => api.escalate(ticket.id, reason ?? ""),
    onSuccess: () => setReason(null),
    onSettled: onChanged,
  });
  const closed = ticket.status === "resolved" || ticket.status === "closed";

  return (
    <div className="card">
      <div className="lbl">Actions</div>
      {reason === null ? (
        <button className="btn2" type="button" style={{ width: "100%" }} disabled={closed} onClick={() => setReason("")}>
          Escalate
        </button>
      ) : (
        <form
          className="stack"
          style={{ gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            escalate.mutate();
          }}
        >
          <label className="field">
            Why escalate?
            <textarea rows={3} required autoFocus value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => setReason(null)}>
              Cancel
            </button>
            <button className="btn" type="submit" disabled={!reason.trim() || escalate.isPending}>
              Escalate
            </button>
          </div>
        </form>
      )}
      {closed && <p className="muted small">Reopen the ticket to escalate it.</p>}
      <div style={{ marginTop: 10 }}>
        {parentNo === null ? (
          <div className="row-gap">
            <button className="linkish small" type="button" onClick={() => setParentNo("")}>
              {ticket.parent ? "Move to another parent ticket…" : "Make it part of another ticket…"}
            </button>
            {ticket.parent && (
              <button className="linkish small" type="button" disabled={reparent.isPending} onClick={() => reparent.mutate(null)}>
                Detach from #{ticket.parent.id}
              </button>
            )}
          </div>
        ) : (
          <form
            className="row-gap"
            onSubmit={(e) => {
              e.preventDefault();
              reparent.mutate(Number(parentNo.replace("#", "")));
            }}
          >
            <label className="field" style={{ flex: 1 }}>
              Parent ticket number
              <input autoFocus inputMode="numeric" placeholder="e.g. 1042" value={parentNo} onChange={(e) => setParentNo(e.target.value)} />
            </label>
            <button className="btn2" type="submit" disabled={!/^#?\d+$/.test(parentNo.trim()) || reparent.isPending}>
              Set
            </button>
            <button className="linkish small" type="button" onClick={() => setParentNo(null)}>
              Cancel
            </button>
          </form>
        )}
        {reparent.error && (
          <p className="error small" role="alert">
            {reparent.error.message}
          </p>
        )}
      </div>
      {!ticket.merged_into && mayMerge && (
        <div style={{ marginTop: 6 }}>
          {merging === null ? (
            <button className="linkish small" type="button" onClick={() => setMerging({ into: "", reason: "", note: "" })}>
              Merge into another ticket…
            </button>
          ) : (
            <form
              className="stack"
              style={{ gap: 8 }}
              aria-label="Merge into another ticket"
              onSubmit={(e) => {
                e.preventDefault();
                if (window.confirm(`Merge #${ticket.id} into #${merging.into.replace("#", "")}? Its replies and files move there and #${ticket.id} is closed.`)) merge.mutate(merging);
              }}
            >
              <label className="field">
                Merge into ticket number
                <input autoFocus inputMode="numeric" placeholder="e.g. 1042" value={merging.into} onChange={(e) => setMerging({ ...merging, into: e.target.value })} />
              </label>
              <label className="field">
                Why
                <select required value={merging.reason} onChange={(e) => setMerging({ ...merging, reason: e.target.value })}>
                  <option value="">Choose a reason…</option>
                  {mergeReasons.data?.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Note <span className="muted small">(optional)</span>
                <input value={merging.note} onChange={(e) => setMerging({ ...merging, note: e.target.value })} />
              </label>
              <div className="row-gap">
                <button className="btn" type="submit" disabled={!/^#?\d+$/.test(merging.into.trim()) || !merging.reason || merge.isPending}>
                  Merge
                </button>
                <button className="btn2" type="button" onClick={() => setMerging(null)}>
                  Cancel
                </button>
              </div>
              {merge.error && (
                <p className="error small" role="alert">
                  {merge.error.message}
                </p>
              )}
            </form>
          )}
        </div>
      )}
      {mayDelete && (
        <div style={{ marginTop: 6 }}>
          <button
            className="linkish small danger"
            type="button"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Delete #${ticket.id} "${ticket.subject}" for good, with its replies, notes, history and files? Closing it keeps the record; deleting is for spam and tests.`))
                remove.mutate();
            }}
          >
            Delete ticket…
          </button>
          {remove.error && (
            <p className="error small" role="alert">
              {remove.error.message}
            </p>
          )}
        </div>
      )}
      {escalate.error && (
        <p className="error" role="alert">
          {escalate.error.message}
        </p>
      )}
    </div>
  );
}
