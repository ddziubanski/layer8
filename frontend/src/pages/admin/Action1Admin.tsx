import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type User } from "../../api/client";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

/** Action1 (patching, read-only): credentials, what the last read found, and machines it couldn't match. */
export function Action1Admin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["action1-status"], queryFn: api.action1.status });
  const unmatched = useQuery({ queryKey: ["action1-machines", "unmatched"], queryFn: () => api.action1.machines("unmatched"), enabled: !!status.data?.machines });
  const test = useMutation({ mutationFn: api.action1.test });
  const sync = useMutation({ mutationFn: () => api.system.runJob("action1"), onSettled: () => setTimeout(() => queryClient.invalidateQueries({ queryKey: ["action1-status"] }), 3000) });
  if (me.role !== "admin") return <p>Action1 is managed by admins.</p>;
  const s = status.data;
  return (
    <>
      <div className="page-head">
        <h1>Action1</h1>
      </div>
      <div className="stack">
        <section className="card stack" style={{ margin: 0 }} aria-label="Patch status">
          <div className="lbl">Patch status from Action1</div>
          <p className="muted" style={{ margin: 0 }}>
            Read only: Layer 8 reads each machine&apos;s missing updates and pending reboot from Action1 and shows them on its device and on tickets. Patching itself
            stays in Action1.
          </p>
          {s && s.machines > 0 && (
            <p className="small" style={{ margin: 0 }}>
              {s.machines} machines in Action1, {s.matched} matched to devices here · <b>{s.critical}</b> missing critical updates · <b>{s.reboot}</b> waiting for a
              reboot
            </p>
          )}
          {s?.last_sync_at && (
            <p className={`small${s.last_sync_ok === false ? " error" : " muted"}`} style={{ margin: 0 }}>
              Last read {when(s.last_sync_at)}: {s.last_sync_ok === false ? s.last_sync_error : s.last_sync_summary}
            </p>
          )}
          <div className="row-gap">
            <button className="btn2" type="button" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
            <button className="btn2" type="button" disabled={sync.isPending || !s?.enabled} onClick={() => sync.mutate()}>
              Read Action1 now
            </button>
            <span className="muted small">
              Ticket thresholds below; tickets go through the <Link to="/settings/rules">routing rules</Link> like any other.
            </span>
          </div>
          {test.data?.ok && (
            <div className="small" role="status">
              Connected. Organizations:{" "}
              {test.data.organizations.map((o) => `${o.name} (${o.id})`).join(", ") || "none"}.
              {test.data.missing_orgs.length > 0 && <span className="error"> Not visible to these credentials: {test.data.missing_orgs.join(", ")}.</span>}
            </div>
          )}
          {test.data && !test.data.ok && (
            <p className="error" role="alert">
              {test.data.error}
            </p>
          )}
          {(unmatched.data?.length ?? 0) > 0 && (
            <details>
              <summary className="small">{unmatched.data!.length} machine(s) in Action1 without a device here</summary>
              <p className="small muted">Matched by name: the Action1 name against a device&apos;s name or host name. These have none (not in AD, or named differently).</p>
              <ul className="small">
                {unmatched.data!.map((m) => (
                  <li key={m.id}>
                    {m.name} <span className="muted">· {m.os} · last seen {m.last_seen_at ? when(m.last_seen_at) : "never"}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
        <SettingsForm me={me} section="action1" title="Connection, tickets and changes" intro="Save the client ID and secret, then use Test connection above: it lists the organizations to put below." embedded onSaved={() => test.reset()} />
      </div>
    </>
  );
}
