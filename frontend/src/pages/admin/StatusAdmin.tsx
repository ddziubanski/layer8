import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Services, type User, type WorkerJob } from "../../api/client";
import { when } from "../../format";

const STATE = { ok: ["g", "OK"], warning: ["a", "Warning"], error: ["r", "Problem"], off: ["n", "Off"] } as const;
const WORKER = { running: ["g", "Running"], not_responding: ["r", "Not responding"], never_started: ["r", "Never started"] } as const;

function jobState(job: WorkerJob): [string, string] {
  if (job.running) return ["a", "Running now"];
  if (job.run_requested) return ["a", "Starting…"];
  if (job.paused) return ["n", "Paused"];
  if (job.last_ok === false) return ["r", "Failed"];
  if (job.last_ok) return ["g", "OK"];
  return ["n", "Not run yet"];
}

/** Settings > Status: how each part is doing, the server processes, and the worker's jobs. */
export function StatusAdmin({ me }: { me: User }) {
  const admin = me.role === "admin";
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["system-status"], queryFn: api.system.status, enabled: admin, refetchInterval: 15_000 });
  // Often, so Run now and restarts are seen as they happen.
  const services = useQuery({ queryKey: ["services"], queryFn: api.system.services, enabled: admin, refetchInterval: 3_000 });
  const done = (data: Services) => queryClient.setQueryData(["services"], data);
  const runJob = useMutation({ mutationFn: api.system.runJob, onSuccess: done });
  const pauseJob = useMutation({ mutationFn: (v: { name: string; paused: boolean }) => api.system.pauseJob(v.name, v.paused), onSuccess: done });
  const restart = useMutation({ mutationFn: api.system.restartWorker, onSuccess: done });

  if (!admin) return <p>Status is for admins.</p>;
  const s = services.data;
  const w = s?.worker;
  const error = runJob.error ?? pauseJob.error ?? restart.error;

  return (
    <>
      <div className="page-head">
        <h1>Status</h1>
      </div>
      <div className="stack">
        <section className="card" style={{ margin: 0 }} aria-label="Overview">
          <div className="lbl">Overview</div>
          {status.error && <p className="error">{status.error.message}</p>}
          <dl className="facts status-list">
            {status.data?.map((item) => (
              <div key={item.key} style={{ display: "contents" }}>
                <dt>{item.label}</dt>
                <dd>
                  <span className={`chip ${STATE[item.state][0]}`}>{STATE[item.state][1]}</span> {item.summary}
                  {item.at && <span className="muted small"> · {when(item.at)}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        {services.error && <p className="error">{services.error.message}</p>}
        {s && w && (
          <>
            <section className="card stack" style={{ margin: 0 }} aria-label="Services">
              <div className="lbl">Services</div>
              <dl className="facts status-list">
                <dt>Web and API server</dt>
                <dd>
                  <span className="chip g">Running</span> on {s.api.host} (process {s.api.pid}), since {when(s.api.started_at)} · version {s.api.version}
                </dd>
                <dt>Background worker</dt>
                <dd>
                  <span className={`chip ${WORKER[w.state][0]}`}>{WORKER[w.state][1]}</span>{" "}
                  {w.state === "never_started"
                    ? "Start the worker service on the server (python -m app.worker)."
                    : `${w.host ? `on ${w.host} (process ${w.pid}), ` : ""}${w.started_at ? `since ${when(w.started_at)}, ` : ""}last checked in ${when(w.last_seen!)}`}
                </dd>
              </dl>
              {w.restart_requested_at && (
                <p className="small" role="status" style={{ margin: 0 }}>
                  Restart asked for by {w.restart_requested_by} at {when(w.restart_requested_at)}
                  {w.state === "running" ? ": the worker restarts when its current job finishes." : ": waiting for the worker to respond."}
                </p>
              )}
              <div className="row-gap">
                <button
                  className="btn2"
                  type="button"
                  disabled={restart.isPending || !!w.restart_requested_at}
                  onClick={() => restart.mutate(false)}
                >
                  Restart worker
                </button>
                {w.can_force_restart && (
                  <button
                    className="btn2"
                    type="button"
                    disabled={restart.isPending}
                    onClick={() => {
                      if (window.confirm("Force-restart the worker service? A job that's running is cut short.")) restart.mutate(true);
                    }}
                  >
                    Force restart
                  </button>
                )}
                <span className="muted small">
                  {w.can_force_restart
                    ? "Restart lets the current job finish. Force restart restarts the service, for a worker that's stuck."
                    : "The worker finishes its current job, then restarts."}
                </span>
              </div>
            </section>

            <section className="card stack" style={{ margin: 0 }} aria-label="Worker jobs">
              <div className="lbl">Worker jobs</div>
              <div className="table-wrap">
                <table className="grid">
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>State</th>
                      <th>Last run</th>
                      <th>Next run</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {s.jobs.map((job) => {
                      const [cls, text] = jobState(job);
                      return (
                        <tr key={job.name}>
                          <td>{job.label}</td>
                          <td>
                            <span className={`chip ${cls}`}>{text}</span>
                          </td>
                          <td>
                            {job.last_finished_at ? when(job.last_finished_at) : "Never"}
                            {job.last_error ? (
                              <div className="error small">{job.last_error}</div>
                            ) : (
                              job.last_summary && <div className="muted small">{job.last_summary}</div>
                            )}
                          </td>
                          <td className="nowrap">{job.paused ? "Paused" : job.next_due_at ? when(job.next_due_at) : "Next check-in"}</td>
                          <td className="nowrap">
                            <div className="row-gap">
                              <button
                                className="btn2"
                                type="button"
                                aria-label={`Run ${job.label} now`}
                                disabled={job.run_requested || job.running || w.state !== "running"}
                                onClick={() => runJob.mutate(job.name)}
                              >
                                Run now
                              </button>
                              <button
                                className="btn2"
                                type="button"
                                aria-label={`${job.paused ? "Resume" : "Pause"} ${job.label}`}
                                onClick={() => pauseJob.mutate({ name: job.name, paused: !job.paused })}
                              >
                                {job.paused ? "Resume" : "Pause"}
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="muted small" style={{ margin: 0 }}>
                A paused job doesn't run on its timer; Run now still runs it once. Run now needs the worker to be running.
              </p>
            </section>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error.message}
          </p>
        )}
      </div>
    </>
  );
}
