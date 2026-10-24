// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ADGroup, type OnboardingTemplate, type OnboardingTemplateIn, type Me } from "../../api/client";
import { GroupLists } from "../../components/GroupLists";
import { GroupPicker } from "../../components/GroupPicker";
import { can } from "../../permissions";

const BLANK: OnboardingTemplateIn = { name: "", description: "", groups: [], ou_dn: "", department: "", title: "", is_active: true };

/** Settings > Onboarding templates: the groups, OU and attributes a new starter (or a move) gets. */
export function OnboardingAdmin({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["onboarding-templates"], queryFn: api.ad.templates });
  const [editing, setEditing] = useState<OnboardingTemplate | "new" | null>(null);
  if (!can(me, "templates_manage")) return <p>Onboarding templates are managed by admins.</p>;
  return (
    <>
      <div className="page-head row-gap">
        <h1 className="spacer">Onboarding templates</h1>
        <button className="btn" type="button" onClick={() => setEditing("new")}>
          New template
        </button>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        A set of security groups, an OU and a department and title. On a ticket, technicians apply one to the requester (with a preview first); it runs on a
        PowerShell runner. Groups and OUs must be within Settings &gt; Directory's allow-lists.
      </p>
      <div className="admin">
        <nav className="admin-list report-list" aria-label="Templates">
          <ul>
            {templates.data?.map((t) => (
              <li key={t.id}>
                <button type="button" aria-current={editing !== "new" && editing?.id === t.id} onClick={() => setEditing(t)}>
                  {t.name}
                  <span className="muted small">
                    {" "}
                    · {t.groups.length} group(s){!t.is_active && " · off"}
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
            onDone={() => (setEditing(null), queryClient.invalidateQueries({ queryKey: ["onboarding-templates"] }))}
          />
        )}
      </div>
    </>
  );
}

function TemplateForm({ template, onDone }: { template: OnboardingTemplate | null; onDone: () => void }) {
  const [t, setT] = useState<OnboardingTemplateIn>(template ?? BLANK);
  const [warnings, setWarnings] = useState<Record<string, string | null>>({});
  const ous = useQuery({ queryKey: ["ad-ous"], queryFn: () => api.ad.ous() });
  const save = useMutation({ mutationFn: () => (template ? api.ad.updateTemplate(template.id, t) : api.ad.createTemplate(t)), onSuccess: onDone });
  const remove = useMutation({ mutationFn: () => api.ad.removeTemplate(template!.id), onSuccess: onDone });
  const groups = t.groups ?? [];
  const add = async (g: ADGroup) => {
    setT({ ...t, groups: [...groups, { dn: g.dn, name: g.name }] });
    try {
      const n = await api.ad.nesting(g.dn);
      setWarnings((w) => ({ ...w, [g.dn]: n.warning ?? null }));
    } catch {
      // The warning is a help, not a must.
    }
  };

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={template ? template.name : "New template"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="field">
        Name
        <input required maxLength={200} value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} placeholder="e.g. Finance – AP clerk" />
      </label>
      <label className="field">
        Description
        <input value={t.description ?? ""} onChange={(e) => setT({ ...t, description: e.target.value })} />
      </label>
      <fieldset className="filter-box">
        <legend>Security groups</legend>
        <div className="stack" style={{ gap: 6 }}>
          {groups.map((g) => (
            <div key={g.dn} className="stack" style={{ gap: 2 }}>
              <div className="row-gap">
                <span className="spacer">
                  {g.name || g.dn} <span className="muted small">{g.dn}</span>
                </span>
                <button className="linkish small" type="button" aria-label={`Remove ${g.name}`} onClick={() => setT({ ...t, groups: groups.filter((x) => x.dn !== g.dn) })}>
                  Remove
                </button>
              </div>
              {warnings[g.dn] && <span className="error small">⚠ {warnings[g.dn]}</span>}
            </div>
          ))}
          <GroupLists
            isTicked={(dn) => groups.some((g) => g.dn.toLowerCase() === dn.toLowerCase())}
            onToggle={(g, on) => (on ? add(g) : setT({ ...t, groups: groups.filter((x) => x.dn.toLowerCase() !== g.dn.toLowerCase()) }))}
          />
          <GroupPicker label="Add a group" exclude={groups.map((g) => g.dn)} onPick={add} />
        </div>
      </fieldset>
      <div className="form-grid">
        <label className="field">
          OU
          <select value={t.ou_dn ?? ""} onChange={(e) => setT({ ...t, ou_dn: e.target.value })}>
            <option value="">Don't move the account</option>
            {ous.data?.map((o) => (
              <option key={o.dn} value={o.dn}>
                {o.dn}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Department
          <input value={t.department ?? ""} onChange={(e) => setT({ ...t, department: e.target.value })} />
        </label>
        <label className="field">
          Job title
          <input value={t.title ?? ""} onChange={(e) => setT({ ...t, title: e.target.value })} />
        </label>
      </div>
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
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${template.name}?`) && remove.mutate()}>
            Delete template
          </button>
        )}
      </div>
    </form>
  );
}
