// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type User } from "../../api/client";
import { fileSize, when } from "../../format";
import { SettingsForm } from "./SettingsForm";

/** Settings > Backup: where, when and how many; the backups there; back up or verify now. Restore is a server command. */
export function BackupAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const files = useQuery({ queryKey: ["backups"], queryFn: api.settings.backups, enabled: me.role === "admin" });
  const services = useQuery({
    queryKey: ["services"],
    queryFn: api.system.services,
    enabled: me.role === "admin",
    // While a backup is asked for or running, watch for it to finish.
    refetchInterval: (q) => (q.state.data?.jobs.some((j) => j.name === "backup" && (j.running || j.run_requested)) ? 3000 : false),
  });
  const job = services.data?.jobs.find((j) => j.name === "backup");
  const runNow = useMutation({ mutationFn: () => api.system.runJob("backup"), onSuccess: (s) => queryClient.setQueryData(["services"], s) });
  const verify = useMutation({ mutationFn: api.settings.verifyBackup });
  const busy = Boolean(job?.running || job?.run_requested);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["backups"] });
  if (me.role !== "admin") return <p>Backups are managed by admins.</p>;

  return (
    <>
      <div className="page-head">
        <h1>Backup</h1>
      </div>
      <div className="stack">
        <SettingsForm
          me={me}
          section="backup"
          title="Schedule"
          embedded
          intro="Each backup is one encrypted file holding the database (tickets, users, settings), attachments, raw emails, branding images and the server's secrets. It's checked after writing; a damaged one is never left in the folder."
          onSaved={refresh}
        />

        <section className="card stack" style={{ margin: 0 }} aria-label="Backups">
          <div className="row-gap">
            <div className="lbl spacer" style={{ margin: 0 }}>
              Backups {files.data?.folder && <span className="muted">in {files.data.folder}</span>}
            </div>
            <button className="btn2" type="button" disabled={busy || runNow.isPending || !files.data?.folder} onClick={() => runNow.mutate()}>
              {busy ? "Backing up…" : "Back up now"}
            </button>
          </div>
          {job?.last_finished_at && (
            <p className={`small ${job.last_ok ? "muted" : "error"}`} style={{ margin: 0 }} role="status">
              Last run {when(job.last_finished_at)}: {job.last_ok ? job.last_summary : job.last_error}
              {job.next_due_at && !busy && <> · next {when(job.next_due_at)}</>}
            </p>
          )}
          {files.data?.problem && <p className="error" style={{ margin: 0 }}>{files.data.problem}</p>}
          {files.data && !files.data.problem && files.data.files.length === 0 && <p className="muted" style={{ margin: 0 }}>No backups yet.</p>}
          {(files.data?.files.length ?? 0) > 0 && (
            <div className="table-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Backup</th>
                    <th>Made</th>
                    <th className="num">Size</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {files.data!.files.map((f) => (
                    <tr key={f.name}>
                      <td className="code">{f.name}</td>
                      <td>{new Date(f.created_at).toLocaleString()}</td>
                      <td className="num">{fileSize(f.size)}</td>
                      <td>
                        <button className="linkish small" type="button" disabled={verify.isPending} onClick={() => verify.mutate(f.name)}>
                          {verify.isPending && verify.variables === f.name ? "Checking…" : "Verify"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {verify.data && (
            <p className="small" role="status" style={{ margin: 0 }}>
              {verify.data.name} is whole: made {new Date(verify.data.created_at).toLocaleString()} on {verify.data.host}, {verify.data.files} files,{" "}
              {verify.data.counts.tickets ?? 0} tickets, {verify.data.counts.users ?? 0} users.
            </p>
          )}
          {(verify.error ?? runNow.error) && (
            <p className="error" role="alert" style={{ margin: 0 }}>
              {(verify.error ?? runNow.error)!.message}
            </p>
          )}
        </section>

        <section className="card stack" style={{ margin: 0 }} aria-label="Restoring">
          <div className="lbl">Restoring</div>
          <p className="muted" style={{ margin: 0 }}>
            Restore on the server, with the services stopped. It checks the whole backup first, then replaces the database, attachments and
            secrets (the old folders are kept beside the new ones) and brings the database up to this version:
          </p>
          <pre className="log-details" style={{ margin: 0 }}>
            {"sudo systemctl stop helpdesk-api helpdesk-worker\npython -m app.cli backup restore /path/to/helpdesk-backup-….hdbackup\nsudo systemctl start helpdesk-api helpdesk-worker"}
          </pre>
          <p className="muted small" style={{ margin: 0 }}>
            On a new server, install the app first, then restore; you'll be asked for the passphrase (or pass --passphrase-file).
          </p>
          <p className="muted small" style={{ margin: 0 }}>
            Moving to a newer version or making a test copy? Use <code>python -m app.cli export</code> on this server and{" "}
            <code>python -m app.cli import --dry-run</code> on the new one: configuration and data without secrets, into an install with no tickets yet.
          </p>
        </section>
      </div>
    </>
  );
}
