// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";

import { api, type SettingField, type User } from "../../api/client";

type Draft = Record<string, string | boolean>;

function initial(field: SettingField): string | boolean {
  if (field.kind === "bool") return Boolean(field.value);
  if (field.kind === "secret") return "";
  if (field.kind === "list") return ((field.value as string[] | null) ?? []).join("\n");
  return field.value == null ? "" : String(field.value);
}

function toValue(field: SettingField, draft: string | boolean): unknown {
  if (field.kind === "list") return String(draft).split("\n");
  return draft;
}

/** One settings section (GET/PUT /api/settings/<section>) as a form. Only changed fields are sent;
 * secrets are never shown, only replaced; fields set on the server are read-only.
 * embedded: a card inside another page, with title as its label instead of a page heading.
 * preview: shown above Save with the values as they'd be saved, so changes can be seen first. */
export function SettingsForm({
  me,
  section,
  title,
  intro,
  embedded = false,
  onSaved,
  preview,
}: {
  me: User;
  section: string;
  title: string;
  intro?: string;
  embedded?: boolean;
  onSaved?: () => void;
  preview?: (values: Record<string, unknown>) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const data = useQuery({ queryKey: ["settings", section], queryFn: () => api.settings.section(section), enabled: me.role === "admin" });
  const [draft, setDraft] = useState<Draft>({});
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.settings.saveSection(section, values),
    onSuccess: (result) => {
      queryClient.setQueryData(["settings", section], result);
      setDraft({});
      onSaved?.();
    },
  });

  if (me.role !== "admin") return <p>Settings are managed by admins.</p>;
  if (!data.data) return data.error ? <p className="error">{data.error.message}</p> : null;
  const fields = data.data.fields;
  const value = (f: SettingField) => (f.name in draft ? draft[f.name] : initial(f));
  const changed = fields.filter((f) => f.name in draft && (f.kind === "secret" ? draft[f.name] !== "" : draft[f.name] !== initial(f)));

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(Object.fromEntries(changed.map((f) => [f.name, toValue(f, draft[f.name])])));
  }

  return (
    <>
      {!embedded && (
        <div className="page-head">
          <h1>{title}</h1>
        </div>
      )}
      <form className="card stack settings-form" style={{ margin: 0 }} onSubmit={submit} aria-label={title}>
        {embedded && <div className="lbl">{title}</div>}
        {intro && (
          <p className="muted" style={{ margin: 0 }}>
            {intro}
          </p>
        )}
        {fields.map((f) => {
          const set = (v: string | boolean) => setDraft((d) => ({ ...d, [f.name]: v }));
          const hint = f.locked ? `Set on the server (HELPDESK_${f.name.toUpperCase()}); change it there.` : f.help;
          if (f.kind === "bool") {
            return (
              <div key={f.name}>
                <label className="inline">
                  <input type="checkbox" checked={value(f) as boolean} disabled={f.locked} onChange={(e) => set(e.target.checked)} /> {f.label}
                </label>
                {hint && <div className="muted small">{hint}</div>}
              </div>
            );
          }
          return (
            <label className="field" key={f.name}>
              {f.label}
              {f.kind === "colour" ? (
                <span className="row-gap">
                  <input type="color" className="colour-swatch" aria-label={`${f.label} picker`} value={value(f) as string} disabled={f.locked} onChange={(e) => set(e.target.value)} />
                  <input className="colour-code" aria-label={f.label} value={value(f) as string} disabled={f.locked} maxLength={7} onChange={(e) => set(e.target.value)} />
                </span>
              ) : f.kind === "list" || f.kind === "pem" || f.kind === "multiline" ? (
                <textarea
                  rows={f.kind === "pem" ? 6 : 3}
                  className={f.kind === "pem" ? "code" : undefined}
                  placeholder={f.kind === "pem" ? "-----BEGIN CERTIFICATE-----" : undefined}
                  value={value(f) as string}
                  disabled={f.locked}
                  onChange={(e) => set(e.target.value)}
                />
              ) : f.kind === "choice" ? (
                <select value={value(f) as string} disabled={f.locked} onChange={(e) => set(e.target.value)}>
                  {f.choices?.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={f.kind === "int" ? "number" : f.kind === "secret" ? "password" : "text"}
                  autoComplete={f.kind === "secret" ? "new-password" : "off"}
                  placeholder={f.kind === "secret" ? (f.is_set ? "Set. Type a new value to replace it" : "Not set") : undefined}
                  value={value(f) as string}
                  disabled={f.locked}
                  onChange={(e) => set(e.target.value)}
                />
              )}
              {hint && <span className="muted small">{hint}</span>}
            </label>
          );
        })}
        {preview?.(Object.fromEntries(fields.map((f) => [f.name, value(f)])))}
        {save.error && (
          <p className="error" role="alert">
            {save.error.message}
          </p>
        )}
        {save.isSuccess && changed.length === 0 && (
          <p className="muted small" role="status">
            Saved.
          </p>
        )}
        <div className="row-gap">
          <button className="btn" type="submit" disabled={changed.length === 0 || save.isPending}>
            Save
          </button>
          {changed.length > 0 && (
            <button className="btn2" type="button" onClick={() => setDraft({})}>
              Undo changes
            </button>
          )}
        </div>
      </form>
    </>
  );
}
