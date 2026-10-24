// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, type Queue, type RuleCatalog, type RuleRun, type TicketRule, type TicketRuleIn, type Me, type User } from "../../api/client";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";
import { can } from "../../permissions";

type Cond = { field: string; op: string; value?: unknown };
type Action = Record<string, unknown> & { type: string };

const TRIGGERS: { key: TicketRuleIn["trigger"]; label: string }[] = [
  { key: "on_create", label: "When a ticket is created" },
  { key: "on_update", label: "When a ticket is updated" },
  { key: "schedule", label: "On a schedule" },
];
const OP_LABEL: Record<string, string> = {
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  contains_any: "contains any of",
  is_empty: "is empty",
  is_set: "is set",
  gt: "more than",
  lt: "less than",
  eq: "is",
  domain: "is in domain",
};

const blank = (): TicketRuleIn => ({
  name: "",
  description: null,
  is_active: true,
  position: 100,
  trigger: "on_create",
  schedule_minutes: null,
  mode: "builder",
  conditions: [],
  select_sql: null,
  actions: [],
  update_sql: null,
  max_rows: 100,
  queue_ids: [],
});

function fromRule(r: TicketRule): TicketRuleIn {
  return { ...r, queue_ids: r.queues.map((q) => q.id) };
}

type Ctx = { catalog: RuleCatalog; queues: Queue[]; agents: User[]; checklists: { id: number; name: string }[] };

/** A list value: enum choices, queues or agents as checkboxes; keywords as comma-separated text. */
function ValueInput({ kind, value, onChange, ctx }: { kind: unknown; value: unknown; onChange: (v: unknown) => void; ctx: Ctx }) {
  const list = Array.isArray(value) ? (value as (string | number)[]) : [];
  const toggle = (v: string | number) => onChange(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  if (Array.isArray(kind) || kind === "queue_ids" || kind === "user_ids") {
    const options: [string | number, string][] = Array.isArray(kind)
      ? kind.map((k: string) => [k, k.replace(/_/g, " ")])
      : kind === "queue_ids"
        ? ctx.queues.map((q) => [q.id, q.name])
        : ctx.agents.map((u) => [u.id, u.display_name]);
    return (
      <div className="checks" style={{ maxHeight: 140 }}>
        {options.map(([v, label]) => (
          <label key={String(v)}>
            <input type="checkbox" checked={list.includes(v)} onChange={() => toggle(v)} /> {label}
          </label>
        ))}
      </div>
    );
  }
  if (kind === "keywords") {
    return <input aria-label="Keywords" placeholder="printer, toner" value={list.join(", ")} onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />;
  }
  if (kind === "minutes") {
    return <input aria-label="Minutes" type="number" min={0} value={typeof value === "number" ? value : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
  }
  return <input aria-label="Value" value={typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value)} />;
}

function Conditions({ value, onChange, ctx, allowChanges }: { value: Cond[]; onChange: (v: Cond[]) => void; ctx: Ctx; allowChanges: boolean }) {
  const fields = { ...ctx.catalog.ticket_fields, ...(allowChanges ? ctx.catalog.change_fields : {}) } as Record<string, { ops: string[]; value: unknown; label: string }>;
  const set = (i: number, c: Cond) => onChange(value.map((x, j) => (j === i ? c : x)));
  // Single-choice fields (e.g. "last reply by") use the list as options for one value.
  const singleChoice = (field: string, op: string) => op === "eq" && Array.isArray(fields[field]?.value);
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.map((c, i) => {
        const spec = fields[c.field];
        const needsValue = !["is_empty", "is_set"].includes(c.op);
        return (
          <div key={i} className="cond">
            <select aria-label="Field" value={c.field} onChange={(e) => set(i, { field: e.target.value, op: fields[e.target.value].ops[0] })}>
              {Object.entries(fields).map(([k, f]) => (
                <option key={k} value={k}>
                  {f.label}
                </option>
              ))}
            </select>
            <select aria-label="Operator" value={c.op} onChange={(e) => set(i, { ...c, op: e.target.value, value: undefined })}>
              {spec?.ops.map((op) => (
                <option key={op} value={op}>
                  {OP_LABEL[op] ?? op}
                </option>
              ))}
            </select>
            <div className="cond-value">
              {needsValue &&
                (singleChoice(c.field, c.op) ? (
                  <select aria-label="Value" value={String(c.value ?? "")} onChange={(e) => set(i, { ...c, value: e.target.value })}>
                    <option value="" disabled>
                      Choose…
                    </option>
                    {(spec.value as string[]).map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                ) : (
                  <ValueInput kind={spec?.value} value={c.value} onChange={(v) => set(i, { ...c, value: v })} ctx={ctx} />
                ))}
            </div>
            <button type="button" className="linkish" aria-label={`Remove condition ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        );
      })}
      <div>
        <button type="button" className="btn2" onClick={() => onChange([...value, { field: "status", op: "in", value: [] }])}>
          Add condition
        </button>
      </div>
    </div>
  );
}

function Actions({ value, onChange, ctx }: { value: Action[]; onChange: (v: Action[]) => void; ctx: Ctx }) {
  const types = ctx.catalog.actions as Record<string, { label: string; fields: Record<string, unknown> }>;
  const set = (i: number, a: Action) => onChange(value.map((x, j) => (j === i ? a : x)));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.map((a, i) => {
        const fields = types[a.type]?.fields ?? {};
        return (
          <div key={i} className="card stack" style={{ gap: 8, padding: 12 }}>
            <div className="row-gap">
              <select aria-label="Action" className="spacer" value={a.type} onChange={(e) => set(i, { type: e.target.value })}>
                {Object.entries(types).map(([k, t]) => (
                  <option key={k} value={k}>
                    {t.label}
                  </option>
                ))}
              </select>
              <button type="button" className="linkish" aria-label={`Remove action ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
            {Object.entries(fields).map(([name, kind]) => {
              if (name === "address" && a.to !== "address") return null;
              const label = name === "value" ? "Value" : name === "for_name" ? "For (a name; empty: the requester)" : name[0].toUpperCase() + name.slice(1);
              const v = a[name];
              if (Array.isArray(kind)) {
                return (
                  <label key={name} className="field">
                    {label}
                    <select value={String(v ?? "")} onChange={(e) => set(i, { ...a, [name]: e.target.value })}>
                      <option value="" disabled>
                        Choose…
                      </option>
                      {kind.map((k: string) => (
                        <option key={k} value={k}>
                          {k.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              }
              if (kind === "checklist_id") {
                return (
                  <label key={name} className="field">
                    Checklist
                    <select value={v == null ? "" : String(v)} onChange={(e) => set(i, { ...a, [name]: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Choose…</option>
                      {ctx.checklists.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              }
              if (kind === "queue_id" || kind === "user_id") {
                const options = kind === "queue_id" ? ctx.queues.map((q) => [q.id, q.name] as const) : ctx.agents.map((u) => [u.id, u.display_name] as const);
                return (
                  <label key={name} className="field">
                    {kind === "queue_id" ? "Queue" : "Agent"}
                    <select value={v == null ? "" : String(v)} onChange={(e) => set(i, { ...a, [name]: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">{kind === "queue_id" ? "Choose…" : "Unassign"}</option>
                      {options.map(([id, n]) => (
                        <option key={id} value={id}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              }
              const multiline = name === "body" || name === "note" || name === "reason";
              return (
                <label key={name} className="field">
                  {label}
                  {multiline ? (
                    <textarea rows={3} value={String(v ?? "")} onChange={(e) => set(i, { ...a, [name]: e.target.value })} />
                  ) : (
                    <input value={String(v ?? "")} onChange={(e) => set(i, { ...a, [name]: e.target.value })} />
                  )}
                </label>
              );
            })}
          </div>
        );
      })}
      <div className="row-gap">
        <button type="button" className="btn2" onClick={() => onChange([...value, { type: "add_note" }])}>
          Add action
        </button>
        <span className="muted small">Templates: {ctx.catalog.template_vars.map((v) => `\${${v}}`).join(" ")}</span>
      </div>
    </div>
  );
}

function RunResult({ run }: { run: RuleRun }) {
  return (
    <div className={`card${run.error ? " note" : ""}`} role="status">
      <div className="lbl">
        {run.dry_run ? "Test run (nothing was changed)" : `Run: ${run.trigger}`} · {when(run.started_at)}
      </div>
      {run.error ? (
        <p className="error" style={{ margin: 0 }}>
          {run.error}
        </p>
      ) : (
        <p style={{ margin: 0 }}>
          Matched {run.matched}, would change {run.changed}
          {run.emails ? `, ${run.emails} email(s)` : ""}.
        </p>
      )}
      {run.details.length > 0 && (
        <ul className="history" style={{ marginTop: 8 }}>
          {run.details.map((d, i) => (
            <li key={i}>
              <Link to={`/tickets/${d.ticket_id}`}>#{String(d.ticket_id)}</Link>
              <span>
                {d.error ? (
                  <span className="error">{String(d.error)}</span>
                ) : (
                  <>
                    {((d.changes as { field: string; before: unknown; after: unknown }[]) ?? []).map((c) => `${c.field}: ${c.before ?? "–"} → ${c.after ?? "–"}`).join("; ") || "no field changes"}
                    {d.notes ? ` · ${d.notes} note(s)` : ""}
                    {d.emails ? ` · ${d.emails} email(s)` : ""}
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RuleEditor({ rule, ctx, onSaved }: { rule: TicketRule | null; ctx: Ctx; onSaved: (r: TicketRule | null) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TicketRuleIn>(() => (rule ? fromRule(rule) : blank()));
  const [testTicket, setTestTicket] = useState("");
  const [result, setResult] = useState<RuleRun | null>(null);
  const runs = useQuery({ queryKey: ["rule-runs", rule?.id], queryFn: () => api.rules.runs(rule!.id), enabled: !!rule });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["rules"] });
    queryClient.invalidateQueries({ queryKey: ["rule-runs", rule?.id] });
  };

  const save = useMutation({
    mutationFn: () => (rule ? api.rules.save(rule.id, form) : api.rules.create(form)),
    onSuccess: (r) => {
      refresh();
      onSaved(r);
    },
  });
  const test = useMutation({ mutationFn: () => api.rules.test(rule!.id, testTicket ? Number(testTicket) : undefined), onSuccess: setResult, onSettled: refresh });
  const run = useMutation({ mutationFn: () => api.rules.run(rule!.id), onSuccess: setResult, onSettled: refresh });
  const remove = useMutation({ mutationFn: () => api.rules.remove(rule!.id), onSuccess: () => { refresh(); onSaved(null); } });
  const [confirmDelete, setConfirmDelete] = useState(false);

  const set = <K extends keyof TicketRuleIn>(k: K, v: TicketRuleIn[K]) => setForm({ ...form, [k]: v });
  const sql = form.mode === "sql";

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h2>{rule ? rule.name : "New rule"}</h2>
      <fieldset className="card stack" style={{ margin: 0 }}>
        <div className="row-gap">
          <label className="field spacer">
            Name
            <input required maxLength={200} value={form.name} onChange={(e) => set("name", e.target.value)} />
          </label>
          <label className="field" style={{ width: 110 }}>
            Order
            <input type="number" value={form.position} onChange={(e) => set("position", Number(e.target.value))} />
          </label>
        </div>
        <label className="field">
          Description
          <input value={form.description ?? ""} onChange={(e) => set("description", e.target.value || null)} />
        </label>
        <div className="row-gap">
          <label className="field spacer">
            Runs
            <select value={form.trigger} onChange={(e) => set("trigger", e.target.value as TicketRuleIn["trigger"])}>
              {TRIGGERS.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {form.trigger === "schedule" && (
            <label className="field" style={{ width: 180 }}>
              Every (minutes)
              <input type="number" min={1} required value={form.schedule_minutes ?? ""} onChange={(e) => set("schedule_minutes", e.target.value ? Number(e.target.value) : null)} />
            </label>
          )}
          <label className="inline" style={{ alignSelf: "end", minHeight: 44 }}>
            <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} /> Active
          </label>
        </div>
        <div>
          <div className="lbl">Queues (none ticked: all)</div>
          <ValueInput kind="queue_ids" value={form.queue_ids} onChange={(v) => set("queue_ids", v as number[])} ctx={ctx} />
        </div>
      </fieldset>

      <fieldset className="card stack" style={{ margin: 0 }}>
        <div className="row-gap">
          <div className="lbl spacer">Which tickets</div>
          <div className="views" role="group" aria-label="Rule mode" style={{ margin: 0 }}>
            <button type="button" aria-pressed={!sql} onClick={() => setForm({ ...form, mode: "builder", select_sql: null, update_sql: null })}>
              Builder
            </button>
            <button type="button" aria-pressed={sql} disabled={!ctx.catalog.sql_enabled} title={ctx.catalog.sql_enabled ? "" : "Needs HELPDESK_RULES_DATABASE_URL"} onClick={() => setForm({ ...form, mode: "sql", conditions: [] })}>
              SQL
            </button>
          </div>
        </div>
        {sql ? (
          <>
            <label className="field">
              Select query <span className="muted small">(must return the ticket <code>id</code>; may use <code>:ticket_ids</code>)</span>
              <textarea className="code" rows={6} spellCheck={false} value={form.select_sql ?? ""} onChange={(e) => set("select_sql", e.target.value)} placeholder="SELECT t.id FROM tickets t WHERE t.status = 'resolved' AND t.status_changed_at < now() - interval '7 days'" />
            </label>
            <label className="field">
              Update query <span className="muted small">(optional; a plain UPDATE of tickets, e.g. <code>WHERE id = ANY(:ticket_ids)</code>. The app applies the changes and logs them.)</span>
              <textarea className="code" rows={4} spellCheck={false} value={form.update_sql ?? ""} onChange={(e) => set("update_sql", e.target.value || null)} placeholder="UPDATE tickets SET status = 'closed' WHERE id = ANY(:ticket_ids)" />
            </label>
          </>
        ) : (
          <Conditions value={form.conditions as Cond[]} onChange={(v) => set("conditions", v)} ctx={ctx} allowChanges={form.trigger === "on_update"} />
        )}
      </fieldset>

      <fieldset className="card stack" style={{ margin: 0 }}>
        <div className="lbl">Then</div>
        <Actions value={form.actions as Action[]} onChange={(v) => set("actions", v)} ctx={ctx} />
        <label className="field" style={{ maxWidth: 260 }}>
          Safety limit: change at most this many tickets per run
          <input type="number" min={1} value={form.max_rows} onChange={(e) => set("max_rows", Number(e.target.value))} />
        </label>
      </fieldset>

      {save.error && (
        <p className="error" role="alert">
          {save.error.message}
        </p>
      )}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          Save rule
        </button>
        {rule && (
          <>
            {rule.trigger !== "schedule" && (
              <input aria-label="Test against ticket" placeholder="Ticket # (optional)" style={{ width: 170 }} value={testTicket} onChange={(e) => setTestTicket(e.target.value.replace(/\D/g, ""))} />
            )}
            <button className="btn2" type="button" disabled={test.isPending} onClick={() => test.mutate()}>
              Test (changes nothing)
            </button>
            {rule.trigger === "schedule" && (
              <button className="btn2" type="button" disabled={run.isPending} onClick={() => run.mutate()}>
                Run now
              </button>
            )}
            <span className="spacer" />
            {confirmDelete ? (
              <>
                <span className="muted small">Delete this rule and its log?</span>
                <button className="btn2" type="button" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
                <button className="btn" type="button" onClick={() => remove.mutate()}>
                  Delete
                </button>
              </>
            ) : (
              <button className="linkish" type="button" onClick={() => setConfirmDelete(true)}>
                Delete rule
              </button>
            )}
          </>
        )}
      </div>
      {(test.error || run.error) && <p className="error">{(test.error ?? run.error)!.message}</p>}
      {result && <RunResult run={result} />}
      {rule && (
        <div className="card">
          <div className="lbl">Run log</div>
          {runs.data && runs.data.length === 0 && <p className="muted small">No runs yet.</p>}
          <ul className="history">
            {runs.data?.map((r) => (
              <li key={r.id}>
                <span className="muted">{when(r.started_at)}</span>
                <span>
                  {r.dry_run ? "test" : r.trigger}: {r.error ? <span className="error">{r.error}</span> : `matched ${r.matched}, changed ${r.changed}${r.emails ? `, ${r.emails} email(s)` : ""}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}

export function RulesAdmin({ me }: { me: Me }) {
  const catalog = useQuery({ queryKey: ["rule-catalog"], queryFn: api.rules.catalog, enabled: can(me, "rules_manage") });
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules.list, enabled: can(me, "rules_manage") });
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const checklists = useQuery({ queryKey: ["checklist-templates"], queryFn: () => api.checklists.templates() });
  const [selected, setSelected] = useState<number | "new" | null>(null);

  if (!can(me, "rules_manage")) return <p>Ticket rules are managed by admins.</p>;
  if (!catalog.data || !rules.data) return catalog.error || rules.error ? <p className="error">{(catalog.error ?? rules.error)!.message}</p> : null;

  const ctx: Ctx = {
    catalog: catalog.data,
    queues: queues.data ?? [],
    agents: (users.data ?? []).filter((u) => u.is_active && u.role !== "requester"),
    checklists: checklists.data ?? [],
  };
  const current = selected === "new" ? null : (rules.data.find((r) => r.id === selected) ?? rules.data[0] ?? null);
  const editing = selected === "new" || current !== null;

  return (
    <>
      <div className="page-head">
        <h1>Ticket rules</h1>
        <span className="spacer" />
        <button className="btn" type="button" onClick={() => setSelected("new")}>
          New rule
        </button>
      </div>
      <div className="admin">
        <ul className="admin-list">
          {rules.data.map((r) => (
            <li key={r.id}>
              <button type="button" aria-current={selected !== "new" && r.id === current?.id} onClick={() => setSelected(r.id)}>
                {r.name}
                <span className="muted small">
                  {" "}
                  · {r.trigger === "schedule" ? `every ${r.schedule_minutes}m` : r.trigger.replace("on_", "on ")}
                  {r.mode === "sql" ? " · SQL" : ""}
                  {!r.is_active ? " · off" : ""}
                </span>
              </button>
            </li>
          ))}
          {rules.data.length === 0 && <li className="muted small">No rules yet.</li>}
        </ul>
        {editing && <RuleEditor key={selected === "new" ? "new" : current!.id} rule={selected === "new" ? null : current} ctx={ctx} onSaved={(r) => setSelected(r ? r.id : null)} />}
      </div>
      <div style={{ marginTop: 20 }}>
        <SettingsForm me={me} section="rules" title="Rule settings" embedded />
      </div>
    </>
  );
}
