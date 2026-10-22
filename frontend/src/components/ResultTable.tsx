import { type ReportResult } from "../api/client";

function formatValue(v: unknown, kind: string): string {
  if (v === null || v === undefined) return "–";
  if (kind === "percent") return `${v}%`;
  if (kind === "money") return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (kind === "hours") return `${v} h`;
  if (kind === "time") return new Date(`${v}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  if (kind === "number") return String(v);
  return String(v);
}

/** The first measure as a bar inside its cell: one hue, the number beside it as text. */
/** A report's or query's result. */
export function ResultTable({ result }: { result: ReportResult }) {
  const numeric = result.columns.findIndex((c) => !["text", "time"].includes(c.kind));
  // An SQL report may return text only: then there's no bar.
  const firstMeasure = numeric === -1 ? result.columns.length : numeric;
  const max = Math.max(0, ...result.rows.map((r) => Number(r[firstMeasure]) || 0));
  const groups = result.columns.slice(0, firstMeasure);
  // Numbers right-aligned; an SQL report's text after its first number stays left.
  const isNum = (c: { kind: string }, i: number) => i >= firstMeasure && !["text", "time"].includes(c.kind);
  return (
    <div className="table-wrap">
      <table className="grid report-table">
        <thead>
          <tr>
            {result.columns.map((c, i) => (
              <th key={c.key} className={isNum(c, i) ? "num" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((v, i) => {
                const col = result.columns[i];
                if (i === firstMeasure && max > 0) {
                  const name = groups.map((g, gi) => formatValue(row[gi], g.kind)).join(" · ");
                  const pct = (Number(v) || 0) / max;
                  return (
                    <td key={col.key} className="num">
                      <div className="bar-cell">
                        <span className="bar-track" aria-hidden="true">
                          <span className="bar" style={{ width: `${pct * 100}%` }} title={`${name}: ${formatValue(v, col.kind)} ${col.label.toLowerCase()}`} />
                        </span>
                        <span className="bar-value">{formatValue(v, col.kind)}</span>
                      </div>
                    </td>
                  );
                }
                return (
                  <td key={col.key} className={isNum(col, i) ? "num" : undefined}>
                    {formatValue(v, col.kind)}
                  </td>
                );
              })}
            </tr>
          ))}
          {result.rows.length === 0 && (
            <tr>
              <td colSpan={result.columns.length} className="muted">
                Nothing in this period.
              </td>
            </tr>
          )}
        </tbody>
        {Object.keys(result.totals).length > 0 && groups.length > 0 && (
          <tfoot>
            <tr>
              <td colSpan={groups.length}>Total</td>
              {result.columns.slice(firstMeasure).map((c) => (
                <td key={c.key} className="num">
                  {c.key in result.totals ? formatValue(result.totals[c.key], c.kind) : ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
