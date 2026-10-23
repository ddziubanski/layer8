import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { api, type Change, type ChangeIn, type ChangeTemplate, type Me } from "../../api/client";
import { StatusChip } from "../../components/Chips";
import { DevicePicker } from "../../components/DevicePicker";
import { ChangeStatusChip, CHANGE_STATUS, RiskChip } from "../../components/ChangeChips";
import { when } from "../../format";

const KIND_LABEL: Record<string, string> = { standard: "Standard (pre-approved)", normal: "Normal", emergency: "Emergency" };
const LEVEL = ["", "Low", "Medium", "High"];

// <input type="datetime-local"> works in local time without a zone.
const toLocalInput = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fromLocalInput = (v: string) => (v ? new Date(v).toISOString() : null);
const window_ = (c: { planned_start?: string | null; planned_end?: string | null }) =>
  c.planned_start ? `${new Date(c.planned_start).toLocaleString()}${c.planned_end ? ` – ${new Date(c.planned_end).toLocaleTimeString()}` : ""}` : "Not planned yet";

/** Changes: the list, newest window first. */
export function ChangesPage() {
  const [view, setView] = useState<"list" | "calendar">("list");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("open");
  const list = useQuery({
    queryKey: ["changes", q, filter],
    queryFn: () => api.changes.list({ q, open_only: filter === "open", status: filter !== "open" && filter !== "all" ? filter : undefined }),
  });
  return (
    <>
      <div className="page-head row-gap">
        <WorkspaceTitle title="Changes" area="CHANGE CONTROL" />
        <Link className="btn" to="/changes/new">
          New change
        </Link>
      </div>
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        {(["list", "calendar"] as const).map((v) => (
          <a
            key={v}
            href={`#${v}`}
            role="tab"
            aria-selected={view === v}
            className={view === v ? "active" : undefined}
            onClick={(e) => {
              e.preventDefault();
              setView(v);
            }}
          >
            {v === "list" ? "List" : "Calendar"}
          </a>
        ))}
      </div>
      {view === "calendar" ? (
        <Calendar />
      ) : (
        <div className="stack">
          <div className="row-gap">
            <input type="search" style={{ maxWidth: 420 }} placeholder="Search title, description or number" aria-label="Search changes" value={q} onChange={(e) => setQ(e.target.value)} />
            <select aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
              <option value="open">Open (draft to in progress)</option>
              <option value="awaiting_approval">Awaiting approval</option>
              <option value="all">All</option>
            </select>
          </div>
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Change</th>
                  <th>Window</th>
                  <th>Risk</th>
                  <th>Status</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {list.data?.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/changes/${c.id}`}>
                        {c.label} {c.title}
                      </Link>
                      {c.kind !== "normal" && <span className="muted small"> · {KIND_LABEL[c.kind]}</span>}
                      {c.source?.startsWith("action1:") && <span className="muted small"> · from Action1</span>}
                    </td>
                    <td className="small">{window_(c)}</td>
                    <td>
                      <RiskChip level={c.risk_level} risk={c.risk} />
                    </td>
                    <td>
                      <ChangeStatusChip status={c.status} />
                      {c.kind === "emergency" && !c.approved_at && ["in_progress", "completed", "failed", "rolled_back"].includes(c.status) && <span className="chip a"> Approve afterwards</span>}
                    </td>
                    <td className="small">{c.owner?.display_name ?? "–"}</td>
                  </tr>
                ))}
                {list.data?.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      {q ? "Nothing matches." : "No changes here. A change is a planned change to systems: a firmware upgrade, a firewall rule, a patch ring."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

/** A month of change windows (weeks from Monday), by start day. */
function Calendar() {
  const [month, setMonth] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const next = new Date(month.getFullYear(), month.getMonth() + 1, 1);
  const list = useQuery({
    queryKey: ["changes", "calendar", month.toISOString()],
    queryFn: () => api.changes.list({ starts_from: month.toISOString(), starts_before: next.toISOString() }),
  });
  const first = new Date(month);
  first.setDate(1 - ((month.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + i));
  const weeks = days[35].getMonth() === month.getMonth() ? 6 : 5;
  const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const byDay = new Map<string, typeof list.data>();
  for (const c of list.data ?? []) {
    const k = key(new Date(c.planned_start!));
    byDay.set(k, [...(byDay.get(k) ?? []), c]);
  }
  const today = key(new Date());
  return (
    <div className="stack">
      <div className="row-gap">
        <button className="btn2" type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label="Previous month">
          ‹
        </button>
        <strong style={{ minWidth: 160, textAlign: "center" }}>{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
        <button className="btn2" type="button" onClick={() => setMonth(next)} aria-label="Next month">
          ›
        </button>
        <span className="muted small">Cancelled and rejected changes are shown struck through.</span>
      </div>
      <div className="change-calendar" role="grid" aria-label="Change calendar">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
          <div key={d} className="cal-head" role="columnheader">
            {d}
          </div>
        ))}
        {days.slice(0, weeks * 7).map((d) => (
          <div key={key(d)} className={`cal-day${d.getMonth() !== month.getMonth() ? " other" : ""}${key(d) === today ? " today" : ""}`} role="gridcell" aria-label={d.toDateString()}>
            <span className="cal-date">{d.getDate()}</span>
            {(byDay.get(key(d)) ?? []).map((c) => (
              <Link
                key={c.id}
                to={`/changes/${c.id}`}
                className={`cal-change ${c.risk_level}${["cancelled", "rejected"].includes(c.status) ? " gone" : ""}`}
                title={`${c.label} ${c.title} · ${CHANGE_STATUS[c.status][0]}`}
              >
                {new Date(c.planned_start!).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} {c.title}
              </Link>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

const BLANK: ChangeIn = {
  title: "",
  kind: "normal",
  template_id: null,
  description: "",
  implementation_plan: "",
  rollback_plan: "",
  test_plan: "",
  impact: 1,
  likelihood: 1,
  planned_start: null,
  planned_end: null,
  owner_id: null,
  ticket_ids: [],
  device_ids: [],
  system_ids: [],
};

function toForm(c: Change): ChangeIn {
  return {
    title: c.title,
    kind: c.kind,
    template_id: c.template_id,
    description: c.description,
    implementation_plan: c.implementation_plan,
    rollback_plan: c.rollback_plan,
    test_plan: c.test_plan,
    impact: c.impact,
    likelihood: c.likelihood,
    planned_start: c.planned_start,
    planned_end: c.planned_end,
    owner_id: c.owner?.id ?? null,
    ticket_ids: c.tickets.map((t) => t.id),
    device_ids: c.devices.map((d) => d.id),
    system_ids: c.systems.map((s) => s.id),
  };
}

/** One change: its plans, risk, window and links, the next steps it can take, and its history. */
export function ChangePage({ me }: { me: Me }) {
  const { changeId } = useParams();
  const isNew = changeId === "new";
  const id = Number(changeId);
  const change = useQuery({ queryKey: ["change", id], queryFn: () => api.changes.get(id), enabled: !isNew });
  if (!isNew && change.error) return <p className="error">{change.error.message}</p>;
  if (!isNew && !change.data) return <p className="muted">Loading…</p>;
  const c = isNew ? null : change.data!;
  return (
    <>
      <div className="page-head row-gap">
        <div className="spacer">
          <div className="small">
            <Link to="/changes">Changes</Link>
          </div>
          <p className="workspace-area">CHANGE WORKSPACE</p>
          <h1 style={{ marginTop: 4 }}>
            {c ? (
              <>
                {c.label} {c.title} <ChangeStatusChip status={c.status} /> <RiskChip level={c.risk_level} risk={c.risk} />
              </>
            ) : (
              "New change"
            )}
          </h1>
        </div>
      </div>
      {c && <dl className="module-summary" aria-label="Change summary"><div><dt>Planned window</dt><dd>{window_(c)}</dd></div><div><dt>Change type</dt><dd>{c.kind}</dd></div><div><dt>Approval</dt><dd>{c.needs_approval ? "Required" : "Not required"}</dd></div></dl>}
      {c && <ActionBar change={c} />}
      {c && c.conflicts.length > 0 && (
        <div className="card note stack" style={{ margin: "0 0 12px", gap: 4 }} role="region" aria-label="Overlapping changes">
          <strong>Other changes in an overlapping window</strong>
          {c.conflicts.map(({ change: o, shared }) => (
            <span key={o.id} className="small">
              <Link to={`/changes/${o.id}`}>
                {o.label} {o.title}
              </Link>{" "}
              <span className="muted">· {window_(o)}</span> <ChangeStatusChip status={o.status} />
              {shared.length > 0 && <strong className="error"> · also touches {shared.join(", ")}</strong>}
            </span>
          ))}
        </div>
      )}
      <div className="po-layout">
        <ChangeForm key={c?.updated_at ?? "new"} change={c} me={me} />
        {c && <History change={c} />}
      </div>
    </>
  );
}

function ActionBar({ change: c }: { change: Change }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const act = useMutation({
    mutationFn: (action: string) => api.changes.act(c.id, action, note),
    onSuccess: (got) => {
      setNote("");
      queryClient.setQueryData(["change", c.id], got);
      queryClient.invalidateQueries({ queryKey: ["changes"] });
    },
  });
  const b = (action: string, label: string, primary = false) => (
    <button key={action} className={primary ? "btn" : "btn2"} type="button" disabled={act.isPending} onClick={() => act.mutate(action)}>
      {label}
    </button>
  );
  const buttons = [];
  const emergencyEarly = c.kind === "emergency" && ["draft", "awaiting_approval"].includes(c.status);
  if (c.status === "draft") buttons.push(b("submit", c.needs_approval ? "Submit for approval" : "Submit (no approval needed)", true));
  if (c.can_approve) buttons.push(b("approve", "Approve", true));
  if (c.can_approve && c.status === "awaiting_approval") buttons.push(b("reject", "Reject"));
  if (c.status === "approved" || emergencyEarly) buttons.push(b("start", emergencyEarly ? "Start now (emergency)" : "Start", c.status === "approved"));
  if (c.status === "in_progress") buttons.push(b("complete", "Completed", true), b("fail", "Failed"), b("roll_back", "Rolled back"));
  if (["draft", "awaiting_approval", "approved", "in_progress"].includes(c.status)) buttons.push(b("cancel", "Cancel change"));
  if (["rejected", "cancelled"].includes(c.status)) buttons.push(b("reopen", "Reopen as draft"));
  const waiting =
    c.status === "awaiting_approval"
      ? c.can_approve
        ? "Waiting for your approval."
        : "Waiting for an approver (Settings > Changes)."
      : c.kind === "emergency" && !c.approved_at && ["in_progress", "completed", "failed", "rolled_back"].includes(c.status)
        ? "Emergency change: an approver approves it afterwards."
        : "";
  return (
    <div className="card stack" style={{ margin: "0 0 12px", padding: 10, gap: 8 }} role="region" aria-label="Next steps">
      <div className="row-gap">
        {buttons}
        {waiting && <span className="muted small">{waiting}</span>}
      </div>
      {buttons.length > 0 && <input aria-label="Note" placeholder="Note: why it's rejected, what happened, anything for the record" value={note} onChange={(e) => setNote(e.target.value)} />}
      {act.error && <p className="error small">{act.error.message}</p>}
    </div>
  );
}

function ChangeForm({ change, me }: { change: Change | null; me: Me }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // From a ticket: the new change starts linked to it.
  const fromTicket = (useLocation().state as { ticketId?: number } | null)?.ticketId;
  const [f, setF] = useState<ChangeIn>(() => (change ? toForm(change) : { ...BLANK, owner_id: me.id, ticket_ids: fromTicket ? [fromTicket] : [] }));
  const [devices, setDevices] = useState<{ id: number; name: string }[]>(change?.devices ?? []);
  const [ticketNo, setTicketNo] = useState("");
  const templates = useQuery({ queryKey: ["change-templates"], queryFn: () => api.changes.templates() });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const systems = useQuery({ queryKey: ["systems", ""], queryFn: () => api.systems.list() });
  const staff = (users.data ?? []).filter((u) => u.role !== "requester" && u.is_active);
  const editable = !change || change.can_edit;
  const body = { ...f, device_ids: devices.map((d) => d.id) };
  const save = useMutation({
    mutationFn: () => (change ? api.changes.update(change.id, body) : api.changes.create(body)),
    onSuccess: (c) => {
      queryClient.setQueryData(["change", c.id], c);
      queryClient.invalidateQueries({ queryKey: ["changes"] });
      if (!change) navigate(`/changes/${c.id}`, { replace: true });
    },
  });
  const applyTemplate = (t: ChangeTemplate | undefined) =>
    t &&
    setF({
      ...f,
      template_id: t.id,
      kind: t.pre_approved ? "standard" : f.kind === "standard" ? "normal" : f.kind,
      title: f.title || t.name,
      description: f.description || t.description,
      implementation_plan: t.implementation_plan,
      rollback_plan: t.rollback_plan,
      test_plan: t.test_plan,
      impact: t.impact,
      likelihood: t.likelihood,
    });
  const text = (key: "implementation_plan" | "rollback_plan" | "test_plan" | "description", label: string, hint: string, rows = 3) => (
    <label className="field">
      {label} <span className="muted small">({hint})</span>
      <textarea rows={rows} disabled={!editable} value={f[key] ?? ""} onChange={(e) => setF({ ...f, [key]: e.target.value })} />
    </label>
  );
  const level = (n: number, label: string, key: "impact" | "likelihood") => (
    <label className="field">
      {label}
      <select disabled={!editable} value={n} onChange={(e) => setF({ ...f, [key]: Number(e.target.value) })}>
        {[1, 2, 3].map((v) => (
          <option key={v} value={v}>
            {LEVEL[v]}
          </option>
        ))}
      </select>
    </label>
  );
  const risk = (f.impact ?? 1) * (f.likelihood ?? 1);

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label="Change"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {!change && (templates.data?.length ?? 0) > 0 && (
        <label className="field">
          Start from a template <span className="muted small">(a pre-approved one makes it a standard change)</span>
          <select value={f.template_id ?? ""} onChange={(e) => applyTemplate(templates.data?.find((t) => t.id === Number(e.target.value)))}>
            <option value="">None</option>
            {templates.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.pre_approved ? " (pre-approved)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Title
        <input required maxLength={300} disabled={!editable} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Core switch firmware 17.1" />
      </label>
      <div className="form-grid">
        <label className="field">
          Kind
          <select disabled={!editable} value={f.kind ?? "normal"} onChange={(e) => setF({ ...f, kind: e.target.value as ChangeIn["kind"] })}>
            <option value="normal">Normal</option>
            <option value="emergency">Emergency (starts now, approved afterwards)</option>
            {f.kind === "standard" && <option value="standard">Standard (pre-approved template)</option>}
          </select>
        </label>
        {level(f.impact ?? 1, "Impact if it goes wrong", "impact")}
        {level(f.likelihood ?? 1, "Likelihood it goes wrong", "likelihood")}
        <div className="field">
          Risk
          <span>
            <RiskChip level={risk <= 2 ? "low" : risk <= 4 ? "medium" : "high"} risk={risk} />
          </span>
        </div>
        <label className="field">
          Starts
          <input type="datetime-local" disabled={!editable} value={toLocalInput(f.planned_start)} onChange={(e) => setF({ ...f, planned_start: fromLocalInput(e.target.value) })} />
        </label>
        <label className="field">
          Ends
          <input type="datetime-local" disabled={!editable} value={toLocalInput(f.planned_end)} onChange={(e) => setF({ ...f, planned_end: fromLocalInput(e.target.value) })} />
        </label>
        <label className="field">
          Owner
          <select disabled={!editable} value={f.owner_id ?? ""} onChange={(e) => setF({ ...f, owner_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">–</option>
            {staff.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {text("description", "What and why", "the reason, what users will notice", 2)}
      {text("implementation_plan", "Implementation plan", "the steps")}
      {text("rollback_plan", "Rollback plan", "required: how to undo it")}
      {text("test_plan", "Test plan", "how you'll know it worked", 2)}
      <fieldset className="filter-box">
        <legend>Linked to</legend>
        <div className="stack" style={{ gap: 6 }}>
          <div className="row-gap">
            {(f.ticket_ids ?? []).map((tid) => {
              const t = change?.tickets.find((x) => x.id === tid);
              return (
                <span key={tid} className="chip n">
                  <Link to={`/tickets/${tid}`}>{t?.label ?? `#${tid}`}</Link> {t?.subject} {t && <StatusChip status={t.status} />}
                  {editable && (
                    <button className="linkish small" type="button" aria-label={`Unlink ticket ${tid}`} onClick={() => setF({ ...f, ticket_ids: (f.ticket_ids ?? []).filter((x) => x !== tid) })}>
                      ×
                    </button>
                  )}
                </span>
              );
            })}
            {editable && (
              <>
                <input aria-label="Link a ticket by number" placeholder="Ticket number" inputMode="numeric" style={{ width: 140 }} value={ticketNo} onChange={(e) => setTicketNo(e.target.value)} />
                <button
                  className="btn2"
                  type="button"
                  disabled={!ticketNo.replace(/\D/g, "")}
                  onClick={() => {
                    const n = Number(ticketNo.replace(/\D/g, ""));
                    if (!(f.ticket_ids ?? []).includes(n)) setF({ ...f, ticket_ids: [...(f.ticket_ids ?? []), n] });
                    setTicketNo("");
                  }}
                >
                  Link ticket
                </button>
              </>
            )}
          </div>
          <div className="row-gap">
            {devices.map((d) => (
              <span key={d.id} className="chip n">
                {d.name}{" "}
                {editable && (
                  <button className="linkish small" type="button" aria-label={`Unlink ${d.name}`} onClick={() => setDevices(devices.filter((x) => x.id !== d.id))}>
                    ×
                  </button>
                )}
              </span>
            ))}
          </div>
          {editable && <DevicePicker label="Link a device" exclude={devices.map((d) => d.id)} onPick={(d) => setDevices([...devices, d])} />}
          <label className="field">
            Systems
            <select
              multiple
              disabled={!editable}
              value={(f.system_ids ?? []).map(String)}
              onChange={(e) => setF({ ...f, system_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}
              style={{ minHeight: 70 }}
            >
              {systems.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </fieldset>
      {change?.outcome && (
        <div className="field">
          What happened
          <p className="body-text small" style={{ margin: 0 }}>
            {change.outcome}
          </p>
        </div>
      )}
      {save.error && <p className="error">{save.error.message}</p>}
      {editable ? (
        <div className="row-gap">
          <button className="btn" type="submit" disabled={save.isPending}>
            {change ? "Save" : "Create change"}
          </button>
          {change?.status === "awaiting_approval" && <span className="muted small">Saving sends it back to draft: it needs approving again.</span>}
        </div>
      ) : (
        <p className="muted small" style={{ margin: 0 }}>
          It can&apos;t be edited once approved; cancel and reopen it to change the plan.
        </p>
      )}
    </form>
  );
}

const EVENT_LABEL: Record<string, string> = {
  created: "Created",
  edited: "Edited",
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  started: "Started",
  completed: "Completed",
  failed: "Failed",
  rolled_back: "Rolled back",
  cancelled: "Cancelled",
  reopened: "Reopened",
};

function History({ change: c }: { change: Change }) {
  return (
    <section className="card stack" style={{ margin: 0, gap: 6 }} aria-label="History">
      <div className="lbl" style={{ margin: 0 }}>
        History
      </div>
      <dl className="facts small">
        <dt>Kind</dt>
        <dd>{KIND_LABEL[c.kind]}</dd>
        {c.source?.startsWith("action1:") && (
          <>
            <dt>Made by</dt>
            <dd>An Action1 patch automation; its run's result ends it</dd>
          </>
        )}
        <dt>Window</dt>
        <dd>{window_(c)}</dd>
        <dt>Raised by</dt>
        <dd>{c.requested_by?.display_name ?? "–"}</dd>
        {c.approved_by && (
          <>
            <dt>Approved by</dt>
            <dd>
              {c.approved_by.display_name}, {when(c.approved_at!)}
            </dd>
          </>
        )}
      </dl>
      <ul className="checklist-items small">
        {[...c.events].reverse().map((e) => (
          <li key={e.id}>
            <strong>{EVENT_LABEL[e.type] ?? e.type}</strong>
            <span className="muted">
              {" "}
              {when(e.at)}
              {e.actor && ` · ${e.actor.display_name}`}
            </span>
            {e.note && <span> · {e.note}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
