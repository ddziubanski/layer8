import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ChangeTemplate, type ChangeTemplateIn, type Me } from "../../api/client";
import { SettingsForm } from "./SettingsForm";
import { can } from "../../permissions";

const BLANK: ChangeTemplateIn = { name: "", pre_approved: false, description: "", implementation_plan: "", rollback_plan: "", test_plan: "", impact: 1, likelihood: 1, is_active: true };
const LEVEL = ["", "Low", "Medium", "High"];

/** Settings > Changes: when changes need approval and who approves; templates (pre-approved ones make standard changes). */
export function ChangesAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["change-templates", "all"], queryFn: () => api.changes.templates(true) });
  const [editing, setEditing] = useState<ChangeTemplate | "new" | null>(null);
  if (!can(me, "templates_manage")) return <p>Changes are set up by admins.</p>;
  return (
    <>
      <div className="page-head">
        <h1>Changes</h1>
      </div>
      <div className="stack">
        {me.role === "admin" && <SettingsForm me={me} section="changes" title="Approval" intro="A change's risk is impact x likelihood. Nobody approves their own change." embedded />}
        <section className="card stack" style={{ margin: 0 }} aria-label="Change templates">
          <div className="row-gap">
            <div className="lbl spacer" style={{ margin: 0 }}>
              Templates
            </div>
            <button className="btn2" type="button" onClick={() => setEditing("new")}>
              New template
            </button>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            A routine change written once: its plans and risk fill a new change. A pre-approved one makes a standard change, approved as soon as it&apos;s submitted
            (e.g. a monthly patch ring).
          </p>
          <ul className="checklist-items">
            {templates.data?.map((t) => (
              <li key={t.id}>
                <button className="linkish" type="button" onClick={() => setEditing(t)}>
                  {t.name}
                </button>
                <span className="muted small">
                  {t.pre_approved ? "pre-approved" : "needs approval as usual"}
                  {!t.is_active && " · off"}
                </span>
              </li>
            ))}
            {templates.data?.length === 0 && <li className="muted small">None yet.</li>}
          </ul>
          {editing && (
            <TemplateForm
              key={editing === "new" ? "new" : editing.id}
              template={editing === "new" ? null : editing}
              onDone={() => (setEditing(null), queryClient.invalidateQueries({ queryKey: ["change-templates"] }))}
            />
          )}
        </section>
      </div>
    </>
  );
}

function TemplateForm({ template, onDone }: { template: ChangeTemplate | null; onDone: () => void }) {
  const [t, setT] = useState<ChangeTemplateIn>(template ?? BLANK);
  const save = useMutation({ mutationFn: () => (template ? api.changes.updateTemplate(template.id, t) : api.changes.createTemplate(t)), onSuccess: onDone });
  const remove = useMutation({ mutationFn: () => api.changes.removeTemplate(template!.id), onSuccess: onDone });
  const text = (key: "description" | "implementation_plan" | "rollback_plan" | "test_plan", label: string) => (
    <label className="field">
      {label}
      <textarea rows={2} value={t[key] ?? ""} onChange={(e) => setT({ ...t, [key]: e.target.value })} />
    </label>
  );
  const level = (key: "impact" | "likelihood", label: string) => (
    <label className="field">
      {label}
      <select value={t[key] ?? 1} onChange={(e) => setT({ ...t, [key]: Number(e.target.value) })}>
        {[1, 2, 3].map((v) => (
          <option key={v} value={v}>
            {LEVEL[v]}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <form
      className="stack filter-box"
      aria-label={template ? template.name : "New template"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          Name
          <input required maxLength={200} value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} placeholder="e.g. Monthly patch ring 1" />
        </label>
        {level("impact", "Impact")}
        {level("likelihood", "Likelihood")}
      </div>
      <label className="inline">
        <input type="checkbox" checked={!!t.pre_approved} onChange={(e) => setT({ ...t, pre_approved: e.target.checked })} /> Pre-approved: changes from it are standard
        changes (no approval each time)
      </label>
      {text("description", "What and why")}
      {text("implementation_plan", "Implementation plan")}
      {text("rollback_plan", "Rollback plan (required for pre-approved)")}
      {text("test_plan", "Test plan")}
      <label className="inline">
        <input type="checkbox" checked={t.is_active ?? true} onChange={(e) => setT({ ...t, is_active: e.target.checked })} /> In use
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {template ? "Save" : "Add template"}
        </button>
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
        <span className="spacer" />
        {template && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${template.name}? Changes made from it stay.`) && remove.mutate()}>
            Delete template
          </button>
        )}
      </div>
    </form>
  );
}
