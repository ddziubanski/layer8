import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type SystemEvent, type User } from "../../api/client";
import { when } from "../../format";

const LEVEL = { info: ["n", "Info"], warning: ["a", "Warning"], error: ["r", "Error"] } as const;
const PAGE = 100;

/** Settings > Log: what happened (the system log). Settings > Status shows what's working now. */
export function SystemLogAdmin({ me }: { me: User }) {
  const admin = me.role === "admin";
  const sources = useQuery({ queryKey: ["log-sources"], queryFn: api.system.logSources, enabled: admin, staleTime: Infinity });
  const [source, setSource] = useState("");
  const [level, setLevel] = useState<"" | "warning" | "error">("");
  const [open, setOpen] = useState<number | null>(null);
  const log = useInfiniteQuery({
    queryKey: ["system-log", source, level],
    queryFn: ({ pageParam }) => api.system.log({ source: source || undefined, level: level || undefined, before_id: pageParam, limit: PAGE }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
    enabled: admin,
    refetchInterval: 30_000,
  });

  if (!admin) return <p>The log is for admins.</p>;
  const label = (s: string) => sources.data?.find((x) => x.value === s)?.label ?? s;
  const rows: SystemEvent[] = log.data?.pages.flat() ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Log</h1>
        <span className="spacer" />
        <button
          className="btn2"
          type="button"
          onClick={() => log.refetch()}
        >
          Refresh
        </button>
      </div>
      <div className="stack">
        <section className="card stack" style={{ margin: 0 }} aria-label="System log">
          <div className="row-gap">
            <div className="lbl" style={{ margin: 0 }}>
              System log
            </div>
            <span className="spacer" />
            <label className="inline small">
              Source{" "}
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">All</option>
                {sources.data?.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="inline small">
              Show{" "}
              <select value={level} onChange={(e) => setLevel(e.target.value as typeof level)}>
                <option value="">Everything</option>
                <option value="warning">Warnings and errors</option>
                <option value="error">Errors only</option>
              </select>
            </label>
          </div>
          {log.error && <p className="error">{log.error.message}</p>}
          <div className="table-wrap">
            <table className="grid log-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Level</th>
                  <th>Source</th>
                  <th>What happened</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap">{when(e.at)}</td>
                    <td>
                      <span className={`chip ${LEVEL[e.level][0]}`}>{LEVEL[e.level][1]}</span>
                    </td>
                    <td className="nowrap">
                      {label(e.source)}
                      {e.process === "worker" && <span className="muted small"> (worker)</span>}
                    </td>
                    <td>
                      {e.message}
                      {e.details && (
                        <>
                          {" "}
                          <button type="button" className="linkish small" onClick={() => setOpen(open === e.id ? null : e.id)}>
                            {open === e.id ? "Hide details" : "Details"}
                          </button>
                          {open === e.id && <pre className="code log-details">{e.details}</pre>}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                {log.isSuccess && rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted">
                      Nothing logged{source || level ? " that matches" : " yet"}.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {log.hasNextPage && (
            <button className="btn2" type="button" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>
              Older entries
            </button>
          )}
          <p className="muted small" style={{ margin: 0 }}>
            Kept for 90 days. Settings changes are logged by name only, never their values.
          </p>
        </section>
      </div>
    </>
  );
}
