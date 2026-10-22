import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type FieldList, type ReportDataset, type ReportSpec } from "../../api/client";

type Choice = { value: string; label: string };

/** The values a filter can take: given by the server for lists, else looked up by kind. */
function useChoices(kind: string, given: Choice[]): Choice[] {
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues, enabled: kind === "queues" });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users, enabled: kind === "agents" });
  const list = kind.startsWith("options:") ? (kind.slice(8) as FieldList) : null;
  const options = useQuery({ queryKey: ["field-options", list, "all"], queryFn: () => api.fieldOptions(list!, true), enabled: list !== null });
  if (kind.startsWith("enum:")) return given;
  if (kind === "queues") return (queues.data ?? []).map((q) => ({ value: String(q.id), label: q.name }));
  if (kind === "agents")
    return [
      { value: "none", label: "Unassigned" },
      ...(users.data ?? []).filter((u) => u.role !== "requester").map((u) => ({ value: String(u.id), label: u.display_name })),
    ];
  if (list) return [{ value: "none", label: "Not set" }, ...(options.data ?? []).map((o) => ({ value: String(o.id), label: o.label }))];
  return [];
}

function FilterField({
  name,
  label,
  kind,
  given,
  values,
  onChange,
}: {
  name: string;
  label: string;
  kind: string;
  given: Choice[];
  values: string[];
  onChange: (v: string[]) => void;
}) {
  const choices = useChoices(kind, given);
  if (kind === "text") {
    return (
      <label className="field">
        {label} <span className="muted small">(comma-separated; blank: any)</span>
        <input
          value={values.join(", ")}
          onChange={(e) =>
            onChange(
              e.target.value
                .split(",")
                .map((v) => v.trim())
                .filter(Boolean),
            )
          }
        />
      </label>
    );
  }
  return (
    <fieldset className="filter-box">
      <legend>
        {label} {values.length > 0 && <span className="muted small">({values.length} chosen)</span>}
      </legend>
      <div className="filter-choices">
        {choices.map((c) => (
          <label key={c.value} className="inline small">
            <input
              type="checkbox"
              name={name}
              checked={values.includes(c.value)}
              onChange={(e) => onChange(e.target.checked ? [...values, c.value] : values.filter((v) => v !== c.value))}
            />
            {c.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

const EXAMPLE_SQL = `SELECT category, count(*) AS tickets
FROM tickets
WHERE created_at >= :start AND created_at < :end
GROUP BY category
ORDER BY tickets DESC`;

/** An SQL report's query: edited here, run when asked (not on every keystroke). */
function SqlEditor({ sql, onRun }: { sql: string; onRun: (sql: string) => void }) {
  const [text, setText] = useState(sql);
  return (
    <div className="stack" style={{ gap: 6 }}>
      <label className="field">
        SQL query
        <textarea
          className="code"
          rows={10}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onRun(text);
          }}
        />
      </label>
      <p className="muted small" style={{ margin: 0 }}>
        One SELECT, read-only, on the restricted rules login (ticket data, no credentials or settings). <code>:start</code> and <code>:end</code> are
        the chosen period (end excluded). Stopped after the SQL rule time limit (Settings &gt; Ticket rules).
      </p>
      <div className="row-gap">
        <button className="btn" type="button" disabled={text === sql} onClick={() => onRun(text)}>
          Run query
        </button>
        <span className="muted small">Ctrl+Enter</span>
      </div>
    </div>
  );
}

/** Change what a report is on, groups by, measures and filters. Changes apply straight away. */
export function Builder({ dataset, spec, onChange }: { dataset: ReportDataset; spec: ReportSpec; onChange: (spec: ReportSpec) => void }) {
  const datasets = useQuery({ queryKey: ["report-datasets"], queryFn: api.reports.datasets, staleTime: Infinity });
  const set = (over: Partial<ReportSpec>) => onChange({ ...spec, ...over });

  return (
    <div className="builder stack" aria-label="Customize report" role="group">
      <label className="field">
        Report on
        <select
          value={spec.dataset ?? "tickets"}
          // Another dataset has other fields: start it fresh.
          onChange={(e) =>
            onChange({ ...spec, dataset: e.target.value, group_by: [], measures: ["count"], filters: {}, date_field: null, sql: e.target.value === "sql" ? EXAMPLE_SQL : null })
          }
        >
          {(datasets.data ?? [{ name: "tickets", label: "Tickets" }]).map((d) => (
            <option key={d.name} value={d.name}>
              {d.label}
            </option>
          ))}
        </select>
      </label>
      {spec.dataset === "sql" ? (
        <>
          <SqlEditor key={spec.sql ?? ""} sql={spec.sql ?? ""} onRun={(sql) => set({ sql })} />
          <label className="field">
            Rows
            <select value={spec.limit ?? 200} onChange={(e) => set({ limit: Number(e.target.value) })}>
              {[10, 25, 50, 100, 200, 500, 1000].map((n) => (
                <option key={n} value={n}>
                  {n === 1000 ? "1000 (most)" : `First ${n}`}
                </option>
              ))}
            </select>
          </label>
        </>
      ) : (
        <GroupsMeasuresFilters dataset={dataset} spec={spec} set={set} />
      )}
    </div>
  );
}

function GroupsMeasuresFilters({ dataset, spec, set }: { dataset: ReportDataset; spec: ReportSpec; set: (over: Partial<ReportSpec>) => void }) {
  const groupBy = spec.group_by ?? [];
  const measures = spec.measures ?? ["count"];
  const filters = spec.filters ?? {};
  return (
    <>
      <div className="row-gap">
        <label className="field spacer">
          Group by
          <select value={groupBy[0] ?? ""} onChange={(e) => set({ group_by: e.target.value ? [e.target.value, ...groupBy.slice(1).filter((g) => g !== e.target.value)] : [] })}>
            <option value="">Nothing (totals only)</option>
            {dataset.dimensions.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field spacer">
          Then by
          <select
            value={groupBy[1] ?? ""}
            disabled={!groupBy[0]}
            onChange={(e) => set({ group_by: e.target.value ? [groupBy[0], e.target.value] : groupBy.slice(0, 1) })}
          >
            <option value="">Nothing</option>
            {dataset.dimensions
              .filter((d) => d.key !== groupBy[0])
              .map((d) => (
                <option key={d.key} value={d.key}>
                  {d.label}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          Rows
          <select value={spec.limit ?? 200} onChange={(e) => set({ limit: Number(e.target.value) })}>
            {[10, 25, 50, 100, 200, 500, 1000].map((n) => (
              <option key={n} value={n}>
                {n === 1000 ? "1000 (most)" : `Top ${n}`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="filter-box">
        <legend>Measures</legend>
        <div className="filter-choices">
          {dataset.measures.map((m) => (
            <label key={m.key} className="inline small">
              <input
                type="checkbox"
                checked={measures.includes(m.key)}
                // At least one measure: the last one can't be unticked.
                disabled={measures.length === 1 && measures.includes(m.key)}
                onChange={(e) => set({ measures: e.target.checked ? dataset.measures.map((x) => x.key).filter((k) => k === m.key || measures.includes(k)) : measures.filter((k) => k !== m.key) })}
              />
              {m.label}
            </label>
          ))}
        </div>
      </fieldset>
      <details>
        <summary>
          Filters{" "}
          {Object.values(filters).some((v) => v.length) && (
            <span className="muted small">({Object.entries(filters).filter(([, v]) => v.length).length} in use)</span>
          )}
        </summary>
        <div className="stack" style={{ marginTop: 10 }}>
          {dataset.filters.map((f) => (
            <FilterField
              key={f.key}
              name={f.key}
              label={f.label}
              kind={f.choices}
              given={f.values ?? []}
              values={filters[f.key] ?? []}
              onChange={(v) => {
                const next = { ...filters, [f.key]: v };
                if (!v.length) delete next[f.key];
                set({ filters: next });
              }}
            />
          ))}
        </div>
      </details>
    </>
  );
}
