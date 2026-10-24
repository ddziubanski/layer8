// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type FieldList, type FieldOption, type User } from "../../api/client";
import { SettingsForm } from "./SettingsForm";

const LISTS: { list: FieldList; title: string; intro: string }[] = [
  { list: "resolution_type", title: "Resolution types", intro: "How a ticket was resolved. Asked when resolving or closing." },
  { list: "closure_reason", title: "Closure reasons", intro: "Why a ticket was closed. Asked when resolving or closing." },
  { list: "merge_reason", title: "Merge reasons", intro: "Why a ticket was merged into another. Asked when merging." },
  { list: "department", title: "Departments", intro: "Shown on each ticket; starts from the requester's AD department." },
];

/** One dropdown list: add, rename, reorder, retire (kept on old tickets) or restore. */
function OptionList({ list, title, intro }: { list: FieldList; title: string; intro: string }) {
  const queryClient = useQueryClient();
  const key = ["field-options", list, "all"];
  const options = useQuery({ queryKey: key, queryFn: () => api.fieldOptions(list, true) });
  const [adding, setAdding] = useState("");
  const [editing, setEditing] = useState<{ id: number; label: string } | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["field-options", list] });
  };
  const add = useMutation({ mutationFn: () => api.addFieldOption(list, adding), onSuccess: () => setAdding(""), onSettled: refresh });
  const update = useMutation({
    mutationFn: (v: { id: number; label?: string; is_active?: boolean }) => api.updateFieldOption(v.id, { label: v.label, is_active: v.is_active }),
    onSuccess: () => setEditing(null),
    onSettled: refresh,
  });
  const reorder = useMutation({ mutationFn: (ids: number[]) => api.reorderFieldOptions(list, ids), onSettled: refresh });
  const imported = useMutation({ mutationFn: api.importDepartments, onSettled: refresh });
  const rows: FieldOption[] = options.data ?? [];
  const error = add.error ?? update.error ?? reorder.error ?? imported.error;

  function move(i: number, by: number) {
    const ids = rows.map((r) => r.id);
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    reorder.mutate(ids);
  }

  return (
    <section className="card stack settings-form" style={{ margin: 0 }} aria-label={title}>
      <div className="row-gap">
        <div className="lbl" style={{ margin: 0 }}>
          {title}
        </div>
        <span className="spacer" />
        {list === "department" && (
          <button className="btn2" type="button" disabled={imported.isPending} onClick={() => imported.mutate()}>
            Import from AD
          </button>
        )}
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        {intro} Retired choices aren't offered any more but stay on old tickets and in reports.
      </p>
      <ul className="option-list">
        {rows.map((o, i) => (
          <li key={o.id} className={o.is_active ? undefined : "retired"}>
            {editing?.id === o.id ? (
              <form
                className="row-gap"
                onSubmit={(e) => {
                  e.preventDefault();
                  update.mutate({ id: o.id, label: editing.label });
                }}
              >
                <input aria-label={`Rename ${o.label}`} autoFocus maxLength={100} value={editing.label} onChange={(e) => setEditing({ id: o.id, label: e.target.value })} />
                <button className="btn2" type="submit">
                  Save
                </button>
                <button className="linkish small" type="button" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </form>
            ) : (
              <>
                <span className="spacer">
                  {o.label}
                  {!o.is_active && <span className="muted small"> (retired)</span>}
                  {o.key && <span className="muted small"> · used by the portal</span>}
                </span>
                <button className="icon-btn" type="button" aria-label={`Move ${o.label} up`} disabled={i === 0 || reorder.isPending} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button className="icon-btn" type="button" aria-label={`Move ${o.label} down`} disabled={i === rows.length - 1 || reorder.isPending} onClick={() => move(i, 1)}>
                  ↓
                </button>
                <button className="linkish small" type="button" onClick={() => setEditing({ id: o.id, label: o.label })}>
                  Rename
                </button>
                <button className="linkish small" type="button" onClick={() => update.mutate({ id: o.id, is_active: !o.is_active })}>
                  {o.is_active ? "Retire" : "Restore"}
                </button>
              </>
            )}
          </li>
        ))}
        {options.isSuccess && rows.length === 0 && <li className="muted small">Nothing yet.</li>}
      </ul>
      <form
        className="row-gap"
        onSubmit={(e) => {
          e.preventDefault();
          if (adding.trim()) add.mutate();
        }}
      >
        <input aria-label={`New ${title.toLowerCase().replace(/s$/, "")}`} placeholder="Add a choice" maxLength={100} value={adding} onChange={(e) => setAdding(e.target.value)} />
        <button className="btn2" type="submit" disabled={!adding.trim() || add.isPending}>
          Add
        </button>
      </form>
      {imported.data && <p className="muted small" role="status">Departments imported from AD.</p>}
      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
    </section>
  );
}

/** Settings > Ticket fields: the dropdown lists on tickets, and which are required. */
export function TicketFieldsAdmin({ me }: { me: User }) {
  if (me.role !== "admin") return <p>Ticket fields are managed by admins.</p>;
  return (
    <>
      <div className="page-head">
        <h1>Ticket fields</h1>
      </div>
      <div className="stack">
        <SettingsForm me={me} section="ticket_fields" title="Rules" embedded />
        {LISTS.map((l) => (
          <OptionList key={l.list} {...l} />
        ))}
      </div>
    </>
  );
}
