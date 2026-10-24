// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ChecklistTemplate, type ChecklistTemplateIn, type Me } from "../../api/client";
import { dateWord } from "../../checklists";
import { can } from "../../permissions";

type Step = NonNullable<ChecklistTemplateIn["items"]>[number];
const KINDS: [string, string, string][] = [
  ["onboarding", "Onboarding", "start date"],
  ["offboarding", "Offboarding", "last day"],
  ["other", "Other", "date"],
];
const BLANK: ChecklistTemplateIn = { name: "", kind: "onboarding", description: "", items: [], is_active: true };
const blankStep = (): Step => ({ title: "", details: "", queue_id: null, assignee_id: null, offset_days: 0, action: null });


/** Settings > Checklists: onboarding, offboarding and other step lists; started on a ticket, each step becomes a child ticket. */
export function ChecklistsAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["checklist-templates", "all"], queryFn: () => api.checklists.templates(true) });
  const [editing, setEditing] = useState<ChecklistTemplate | "new" | null>(null);
  if (!can(me, "templates_manage")) return <p>Checklists are managed by admins.</p>;
  return (
    <>
      <div className="page-head row-gap">
        <h1 className="spacer">Checklists</h1>
        <button className="btn" type="button" onClick={() => setEditing("new")}>
          New checklist
        </button>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Steps for a new starter, a leaver, an office move... Started on a ticket, each step becomes a child ticket, due a number of days from the start date or last
        day, in its own queue or with its own technician. An offboarding step can disable the leaver&apos;s AD account on its day (on a PowerShell relay).
      </p>
      <div className="admin">
        <nav className="admin-list report-list" aria-label="Checklists">
          <ul>
            {templates.data?.map((t) => (
              <li key={t.id}>
                <button type="button" aria-current={editing !== "new" && editing?.id === t.id} onClick={() => setEditing(t)}>
                  {t.name}
                  <span className="muted small">
                    {" "}
                    · {KINDS.find(([k]) => k === t.kind)?.[1]} · {t.items.length} step(s){!t.is_active && " · off"}
                  </span>
                </button>
              </li>
            ))}
            {templates.data?.length === 0 && <li className="muted small">None yet.</li>}
          </ul>
        </nav>
        {editing && (
          <TemplateForm
            key={editing === "new" ? "new" : editing.id}
            template={editing === "new" ? null : editing}
            onDone={() => (setEditing(null), queryClient.invalidateQueries({ queryKey: ["checklist-templates"] }))}
          />
        )}
      </div>
    </>
  );
}

function TemplateForm({ template, onDone }: { template: ChecklistTemplate | null; onDone: () => void }) {
  const [t, setT] = useState<ChecklistTemplateIn>(template ?? BLANK);
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const staff = (users.data ?? []).filter((u) => u.role !== "requester" && u.is_active);
  const save = useMutation({ mutationFn: () => (template ? api.checklists.updateTemplate(template.id, t) : api.checklists.createTemplate(t)), onSuccess: onDone });
  const remove = useMutation({ mutationFn: () => api.checklists.removeTemplate(template!.id), onSuccess: onDone });
  const steps = t.items ?? [];
  const setStep = (i: number, over: Partial<Step>) => setT({ ...t, items: steps.map((s, j) => (j === i ? { ...s, ...over } : s)) });
  const move = (i: number, j: number) => {
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    setT({ ...t, items: next });
  };
  const word = dateWord(t.kind ?? "other");

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0, maxWidth: "none" }}
      aria-label={template ? template.name : "New checklist"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          Name
          <input required maxLength={200} value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} placeholder="e.g. New starter, Leaver, Office move" />
        </label>
        <label className="field">
          Kind
          <select value={t.kind ?? "other"} onChange={(e) => setT({ ...t, kind: e.target.value as ChecklistTemplateIn["kind"] })}>
            {KINDS.map(([k, label, w]) => (
              <option key={k} value={k}>
                {label} (from the {w})
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        What it&apos;s for
        <input value={t.description ?? ""} onChange={(e) => setT({ ...t, description: e.target.value })} />
      </label>
      <fieldset className="filter-box">
        <legend>Steps</legend>
        <div className="stack" style={{ gap: 10 }}>
          {steps.map((s, i) => (
            <div key={i} className="stack checklist-step" role="group" aria-label={`Step ${i + 1}`}>
              <div className="row-gap">
                <input aria-label="Step" className="spacer" required placeholder="e.g. Order laptop" value={s.title} onChange={(e) => setStep(i, { title: e.target.value })} />
                <button className="linkish small" type="button" disabled={i === 0} onClick={() => move(i, i - 1)} aria-label={`Move step ${i + 1} up`}>
                  Up
                </button>
                <button className="linkish small" type="button" disabled={i === steps.length - 1} onClick={() => move(i, i + 1)} aria-label={`Move step ${i + 1} down`}>
                  Down
                </button>
                <button className="linkish small" type="button" onClick={() => setT({ ...t, items: steps.filter((_, j) => j !== i) })} aria-label={`Remove step ${i + 1}`}>
                  Remove
                </button>
              </div>
              <div className="form-grid">
                <label className="field">
                  Due (days from the {word})
                  <input type="number" min={-365} max={365} value={s.offset_days ?? 0} onChange={(e) => setStep(i, { offset_days: Number(e.target.value) })} />
                </label>
                <label className="field">
                  Queue
                  <select value={s.queue_id ?? ""} onChange={(e) => setStep(i, { queue_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">The ticket&apos;s queue</option>
                    {queues.data?.map((q) => (
                      <option key={q.id} value={q.id}>
                        {q.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  Technician
                  <select value={s.assignee_id ?? ""} onChange={(e) => setStep(i, { assignee_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">Unassigned</option>
                    {staff.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.display_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  It also
                  <select value={s.action ?? ""} onChange={(e) => setStep(i, { action: (e.target.value || null) as Step["action"] })}>
                    <option value="">Nothing (a ticket only)</option>
                    <option value="disable_ad_account">Disables the person&apos;s AD account on its day</option>
                  </select>
                </label>
              </div>
              <label className="field">
                Details <span className="muted small">(what to do; goes in the step&apos;s ticket)</span>
                <textarea rows={2} value={s.details ?? ""} onChange={(e) => setStep(i, { details: e.target.value })} />
              </label>
            </div>
          ))}
          <div>
            <button className="btn2" type="button" onClick={() => setT({ ...t, items: [...steps, blankStep()] })}>
              Add a step
            </button>
          </div>
        </div>
      </fieldset>
      <label className="inline">
        <input type="checkbox" checked={t.is_active ?? true} onChange={(e) => setT({ ...t, is_active: e.target.checked })} /> In use
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {template ? "Save" : "Add checklist"}
        </button>
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
        <span className="spacer" />
        {template && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${template.name}? Checklists already started keep their steps.`) && remove.mutate()}>
            Delete checklist
          </button>
        )}
      </div>
    </form>
  );
}
