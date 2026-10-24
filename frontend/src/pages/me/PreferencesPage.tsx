// SPDX-License-Identifier: AGPL-3.0-or-later
import { TwoFactorSection } from "../../components/TwoFactor";
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { TeamsConnection } from "../../components/TeamsConnection";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ColorRule, type Me } from "../../api/client";
import { PALETTE, RULE_FIELDS } from "../../colors";
import { UserPicker } from "../../components/UserPicker";
import { ME_KEY } from "../../queryClient";
import { MINIMAL_HIDDEN, OPTIONAL_MODULES, moduleEnabled, type SidebarModule } from "../../navigation";

import { DEFAULT_TICKET_WORKSPACE, TICKET_TOOLS, type TicketWorkspacePreference } from "../ticket/TicketToolsWorkspace";

function Swatches({ value, onChange, label }: { value: string | null; onChange: (c: string) => void; label: string }) {
  return (
    <div className="row-gap" role="group" aria-label={label}>
      {PALETTE.map((c) => (
        <button
          key={c}
          type="button"
          className="swatch"
          style={{ background: c }}
          aria-label={c}
          aria-pressed={value === c}
          onClick={() => onChange(c)}
        />
      ))}
      <input type="color" className="colour-swatch" aria-label={`${label}: other`} value={value ?? "#2563eb"} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/** My preferences: my colour in the queue (everyone sees it), and my own colour rules (only I do). */
export function PreferencesPage({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false });
  const activeMe = profile.data?.id === me.id ? profile.data : me;
  const [color, setColor] = useState<string | null>(activeMe.ticket_color ?? null);
  const [rules, setRules] = useState<ColorRule[]>(activeMe.color_rules ?? []);
  const [hidden, setHidden] = useState<SidebarModule[]>(activeMe.hidden_modules ?? []);
  const [workspace, setWorkspace] = useState<TicketWorkspacePreference>(activeMe.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE);
  const workspaceChanged = JSON.stringify(workspace) !== JSON.stringify(activeMe.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE);
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const [names, setNames] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => api.savePreferences({ ticket_color: color, color_rules: rules, hidden_modules: hidden, ...(workspaceChanged ? { ticket_workspace: workspace } : {}) }),
    onSuccess: (m) => queryClient.setQueryData(ME_KEY, m),
  });
  const setRule = (i: number, over: Partial<ColorRule>) => setRules(rules.map((r, j) => (j === i ? { ...r, ...over } : r)));
  const move = (i: number, by: number) => {
    const next = [...rules];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    setRules(next);
  };
  // What's stored now: the last save, else what the page opened with.
  const stored = save.data ?? activeMe;
  const changed = workspaceChanged || color !== (stored.ticket_color ?? null) || JSON.stringify(rules) !== JSON.stringify(stored.color_rules ?? [])
    || JSON.stringify([...hidden].sort()) !== JSON.stringify([...(stored.hidden_modules ?? [])].sort());
  const isStaff = me.role !== "requester";

  return (
    <>
      <div className="page-head">
        <WorkspaceTitle title="My preferences" area="YOUR WORKSPACE" />
      </div>
      <div className="stack" style={{ maxWidth: 900 }}>
        {isStaff && <TwoFactorSection />}
        {isStaff && <section className="card stack" aria-label="My workspace">
          <div className="lbl" style={{ margin: 0 }}>My workspace</div>
          <p className="small muted">Choose what appears in your sidebar. This follows your account across browsers. Personally hidden modules remain available through Ctrl+K / ⌘K or a direct link, while globally disabled workspace modules stay unavailable.</p>
          <div className="row-gap">
            <button className="btn2" type="button" disabled={save.isPending} onClick={() => setHidden([...MINIMAL_HIDDEN])}>Use minimal workspace</button>
            <button className="btn2" type="button" disabled={save.isPending} onClick={() => setHidden([])}>Show all modules</button>
          </div>
          <p className="small">Minimal keeps Tickets, Dashboard, Devices and Knowledge base. Tickets, My preferences and sign-out are always available. Save below to apply.</p>
          <fieldset className="stack" style={{ gap: 6, border: 0, padding: 0, margin: 0 }}>
            <legend>Show in my sidebar</legend>
            {OPTIONAL_MODULES.map(module => <label className="inline" key={module.id}><input type="checkbox" checked={moduleEnabled(activeMe, module.id) && !hidden.includes(module.id)} disabled={save.isPending || !moduleEnabled(activeMe, module.id)}
              onChange={e => setHidden(e.target.checked ? hidden.filter(id => id !== module.id) : [...hidden, module.id])} />{module.label}{!moduleEnabled(activeMe, module.id) && <span className="muted small"> — turned off by administrator</span>}</label>)}
          </fieldset>
        </section>}
        {isStaff && <section className="card stack" aria-label="Ticket workspace">
          <h2>Ticket workspace</h2>
          <p className="muted small">Layout changes apply when you next open a ticket. Arrange this page still controls which panels are visible.</p>
          <label className="field">Layout<select value={workspace.mode} onChange={e => setWorkspace({ ...workspace, mode: e.target.value as TicketWorkspacePreference["mode"] })}><option value="drawer">Conversation with tools drawer</option><option value="classic">Classic arranged panels</option></select></label>
          <label className="field">Preferred tool<select value={workspace.selected_tool} onChange={e => setWorkspace({ ...workspace, selected_tool: e.target.value as TicketWorkspacePreference["selected_tool"] })}>{TICKET_TOOLS.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
          <label className="inline"><input type="checkbox" checked={workspace.keep_open} onChange={e => setWorkspace({ ...workspace, keep_open: e.target.checked })} />Keep tools open on new tickets</label>
        </section>}
        {isStaff && <TeamsConnection />}
        {isStaff && (
          <section className="card stack" aria-label="My colour">
            <div className="lbl" style={{ margin: 0 }}>
              My colour
            </div>
            <p className="muted small" style={{ margin: 0 }}>
              Tickets assigned to you get a stripe in this colour in everyone's queue, so whose ticket is whose shows at a glance.
            </p>
            <Swatches label="My colour" value={color} onChange={setColor} />
            {color && (
              <button className="linkish small" type="button" style={{ alignSelf: "flex-start" }} onClick={() => setColor(null)}>
                No colour
              </button>
            )}
          </section>
        )}
        <section className="card stack" aria-label="My colour rules">
          <div className="lbl" style={{ margin: 0 }}>
            My colour rules
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Tint rows in your own queue. Only you see these. A ticket takes the first rule it matches.
          </p>
          {rules.length === 0 && <p className="muted" style={{ margin: 0 }}>No rules yet.</p>}
          {rules.map((r, i) => {
            const field = RULE_FIELDS.find((f) => f.key === r.field)!;
            return (
              <div key={i} className="rule-row" role="group" aria-label={`Rule ${i + 1}`} style={{ borderLeftColor: r.color }}>
                <select aria-label="When" value={r.field} onChange={(e) => setRule(i, { field: e.target.value as ColorRule["field"], value: RULE_FIELDS.find((f) => f.key === e.target.value)!.values?.[0][0] ?? "" })}>
                  {RULE_FIELDS.map((f) => (
                    <option key={f.key} value={f.key}>
                      {f.label}
                    </option>
                  ))}
                </select>
                {field.values && (
                  <select aria-label="Value" value={r.value} onChange={(e) => setRule(i, { value: e.target.value })}>
                    {field.values.map(([v, label]) => (
                      <option key={v} value={v}>
                        {label}
                      </option>
                    ))}
                  </select>
                )}
                {r.field === "queue" && (
                  <select aria-label="Value" value={r.value} onChange={(e) => setRule(i, { value: e.target.value })}>
                    <option value="">Choose…</option>
                    {queues.data?.map((q) => (
                      <option key={q.id} value={q.id}>
                        {q.name}
                      </option>
                    ))}
                  </select>
                )}
                {r.field === "requester" && (
                  <div style={{ minWidth: 240 }}>
                    <UserPicker
                      label="Who"
                      value={r.value ? { id: Number(r.value), display_name: names[r.value] ?? `Person #${r.value}`, email: "" } : null}
                      onChange={(u) => {
                        if (u) setNames({ ...names, [String(u.id)]: u.display_name });
                        setRule(i, { value: u ? String(u.id) : "" });
                      }}
                    />
                  </div>
                )}
                <Swatches label={`Rule ${i + 1} colour`} value={r.color} onChange={(c) => setRule(i, { color: c })} />
                <span className="spacer" />
                <button className="linkish small" type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move rule ${i + 1} up`}>
                  ↑
                </button>
                <button className="linkish small" type="button" disabled={i === rules.length - 1} onClick={() => move(i, 1)} aria-label={`Move rule ${i + 1} down`}>
                  ↓
                </button>
                <button className="linkish small" type="button" onClick={() => setRules(rules.filter((_, j) => j !== i))}>
                  Remove
                </button>
              </div>
            );
          })}
          <div>
            <button className="btn2" type="button" disabled={rules.length >= 30} onClick={() => setRules([...rules, { field: "priority", value: "critical", color: "#dc2626" }])}>
              Add a rule
            </button>
          </div>
        </section>
        {save.error && <p className="error">{save.error.message}</p>}
        {save.isSuccess && !changed && (
          <p className="muted small" role="status">
            Saved.
          </p>
        )}
        <div className="row-gap">
          <button className="btn" type="button" disabled={!changed || save.isPending || rules.some((r) => !r.value)} onClick={() => save.mutate()}>
            Save
          </button>
          {changed && (
            <button className="btn2" type="button" onClick={() => { setColor(stored.ticket_color ?? null); setRules(stored.color_rules ?? []); setHidden(stored.hidden_modules ?? []); setWorkspace(stored.ticket_workspace ?? DEFAULT_TICKET_WORKSPACE); }}>
              Undo changes
            </button>
          )}
        </div>
      </div>
    </>
  );
}
