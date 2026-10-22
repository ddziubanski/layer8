import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Runner, type RunnerIn, type RunnerToken, type User } from "../../api/client";
import { when } from "../../format";

const LANGUAGES: [RunnerIn["languages"][number], string][] = [
  ["powershell", "PowerShell"],
  ["bash", "bash"],
  ["python", "Python"],
  ["eos", "Arista EOS"],
];

/** Settings > Runners: the machines that run action scripts. Each polls the service desk with its own token. */
export function RunnersAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const runners = useQuery({ queryKey: ["runners"], queryFn: api.runners.list, enabled: me.role === "admin", refetchInterval: 30_000 });
  const [editing, setEditing] = useState<Runner | "new" | null>(null);
  const [token, setToken] = useState<RunnerToken | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["runners"] });
  const newToken = useMutation({ mutationFn: api.runners.newToken, onSuccess: setToken, onSettled: refresh });
  const remove = useMutation({ mutationFn: api.runners.remove, onSettled: refresh });
  if (me.role !== "admin") return <p>Runners are managed by admins.</p>;

  return (
    <>
      <div className="page-head row-gap">
        <h1 className="spacer">Runners</h1>
        <button className="btn" type="button" onClick={() => (setEditing("new"), setToken(null))}>
          Add a runner
        </button>
      </div>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          A runner runs action scripts: for PowerShell, a domain-joined Windows machine whose service account may use WinRM on the PCs. It checks in over HTTPS
          (no inbound ports) with its own token.
        </p>
        {token && <TokenCard token={token} onDone={() => setToken(null)} />}
        {editing && (
          <RunnerForm
            runner={editing === "new" ? null : editing}
            onDone={(made) => {
              setEditing(null);
              if (made) setToken(made);
              refresh();
            }}
          />
        )}
        {runners.error && <p className="error">{runners.error.message}</p>}
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Runner</th>
                <th>Runs</th>
                <th>Last check-in</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runners.data?.map((r) => {
                const online = r.last_seen_at && Date.now() - new Date(r.last_seen_at).getTime() < 3 * 60_000;
                return (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.name}</strong> {!r.is_active && <span className="chip n">Off</span>}
                      {r.description && <div className="muted small">{r.description}</div>}
                    </td>
                    <td>{r.languages.map((l) => LANGUAGES.find(([k]) => k === l)?.[1] ?? l).join(", ")}</td>
                    <td>
                      {r.last_seen_at ? (
                        <>
                          <span className={`chip ${online ? "g" : "a"}`}>{online ? "Online" : "Quiet"}</span> {when(r.last_seen_at)}
                          <div className="muted small">
                            {r.last_host} · runner {r.last_version}
                          </div>
                        </>
                      ) : (
                        <span className="muted">Never</span>
                      )}
                    </td>
                    <td className="nowrap">
                      <button className="linkish small" type="button" onClick={() => (setEditing(r), setToken(null))}>
                        Edit
                      </button>{" "}
                      <button
                        className="linkish small"
                        type="button"
                        onClick={() => {
                          if (window.confirm(`Give ${r.name} a new token? The old one stops working at once.`)) newToken.mutate(r.id);
                        }}
                      >
                        New token
                      </button>{" "}
                      <button
                        className="linkish small"
                        type="button"
                        onClick={() => {
                          if (window.confirm(`Remove ${r.name}? Its token stops working.`)) remove.mutate(r.id);
                        }}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
              {runners.data?.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No runners yet: action scripts wait until one is added and running.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {(newToken.error ?? remove.error) && <p className="error">{(newToken.error ?? remove.error)!.message}</p>}
      </div>
    </>
  );
}

function RunnerForm({ runner, onDone }: { runner: Runner | null; onDone: (made?: RunnerToken) => void }) {
  const [r, setR] = useState<RunnerIn>(runner ? { name: runner.name, description: runner.description, languages: runner.languages as RunnerIn["languages"], is_active: runner.is_active } : { name: "", description: "", languages: ["powershell"], is_active: true });
  const save = useMutation({
    mutationFn: async () => (runner ? (await api.runners.update(runner.id, r), undefined) : api.runners.create(r)),
    onSuccess: (made) => onDone(made ?? undefined),
  });
  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={runner ? `Edit ${runner.name}` : "Add a runner"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="lbl">{runner ? `Edit ${runner.name}` : "Add a runner"}</div>
      <label className="field">
        Name
        <input required maxLength={100} value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="e.g. RUNNER01" />
      </label>
      <label className="field">
        Description <span className="muted small">(optional)</span>
        <input value={r.description ?? ""} onChange={(e) => setR({ ...r, description: e.target.value })} />
      </label>
      <fieldset className="filter-box">
        <legend>Runs</legend>
        <div className="filter-choices">
          {LANGUAGES.map(([k, label]) => (
            <label key={k} className="inline small">
              <input
                type="checkbox"
                checked={r.languages.includes(k)}
                onChange={(e) => setR({ ...r, languages: e.target.checked ? [...r.languages, k] : r.languages.filter((x) => x !== k) })}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="inline">
        <input type="checkbox" checked={r.is_active ?? true} onChange={(e) => setR({ ...r, is_active: e.target.checked })} /> In use
      </label>
      {save.error && <p className="error">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending || r.languages.length === 0}>
          {runner ? "Save" : "Add runner"}
        </button>
        <button className="btn2" type="button" onClick={() => onDone()}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** The token, once, and how to start the runner with it. */
function TokenCard({ token, onDone }: { token: RunnerToken; onDone: () => void }) {
  const url = window.location.origin;
  return (
    <section className="card note stack" style={{ margin: 0 }} aria-label="Runner token">
      <strong>Token for {token.runner.name}: copy it now, it isn't shown again.</strong>
      <pre className="log-details code" style={{ margin: 0 }}>
        {token.token}
      </pre>
      <span className="small">
        On the runner (Python 3.10+): save the token to a file only the runner's service account can read, copy <code>runner/helpdesk_runner.py</code>, and
        run it as a service:
      </span>
      <pre className="log-details code" style={{ margin: 0 }}>
        {`python helpdesk_runner.py --url ${url} --token-file C:\\ProgramData\\HelpdeskRunner\\token`}
      </pre>
      <div>
        <button className="btn2" type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </section>
  );
}
