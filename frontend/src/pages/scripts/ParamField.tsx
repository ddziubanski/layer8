// SPDX-License-Identifier: AGPL-3.0-or-later
import { type ScriptParam } from "../../api/client";

/** One script parameter as a form field. */
export function ParamField({ param, value, onChange }: { param: ScriptParam; value: string | boolean; onChange: (v: string | boolean) => void }) {
  const label = `${param.label || param.name}${param.required ? "" : " (optional)"}`;
  if (param.type === "bool") {
    return (
      <label className="inline">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} /> {label}
      </label>
    );
  }
  return (
    <label className="field">
      {label}
      {param.type === "choice" ? (
        <select value={String(value)} required={param.required} onChange={(e) => onChange(e.target.value)}>
          {!param.required && <option value="">–</option>}
          {(param.choices ?? []).map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      ) : (
        <input type={param.type === "int" ? "number" : param.type === "date" ? "date" : "text"} required={param.required} value={String(value)} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}
