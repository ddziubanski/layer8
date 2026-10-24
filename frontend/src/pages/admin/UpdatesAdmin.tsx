// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type User, type UpdateStatus } from "../../api/client";
import { fileSize, when } from "../../format";
import { SettingsForm } from "./SettingsForm";

/** No network check on page load; each installation is reviewed and explicitly confirmed. */
export function UpdatesAdmin({ me }: { me: User }) {
  const client = useQueryClient();
  const [review, setReview] = useState<UpdateStatus["release"] | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const query = useQuery({ queryKey: ["release-updates"], queryFn: api.updates.status, enabled: me.role === "admin", refetchInterval: 10000, retry: false });
  const progressUrl = query.data?.progress_url;
  const progress = useQuery({ queryKey: ["update-progress", progressUrl], queryFn: () => api.updates.progress(progressUrl!), enabled: !!progressUrl && me.role === "admin", refetchInterval: 3000, retry: false });
  const operation = progress.data ?? query.data?.operation ?? {};
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (!query.data?.busy) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [query.data?.busy]);
  const started = typeof operation.started_at === "string" ? Date.parse(operation.started_at) : NaN;
  const ended = ["succeeded", "failed", "rolled_back", "recovery_required"].includes(String(operation.phase)) && typeof operation.updated_at === "string" ? Date.parse(operation.updated_at) : now;
  const elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((ended - started) / 1000)) : null;
  const checks = Array.isArray(operation.checks) ? operation.checks as { name: string; state: string; detail: string }[] : [];
  const events = Array.isArray(operation.events) ? operation.events as { at: string; message: string }[] : [];
  const download = operation.download as { received: number; total: number } | null;
  const save = (data: UpdateStatus) => client.setQueryData(["release-updates"], data);
  const check = useMutation({ mutationFn: api.updates.check, onSuccess: data => { save(data); setReview(null); setConfirmed(false); } });
  const apply = useMutation({ mutationFn: () => api.updates.apply({ version: review!.version, manifest_sha256: review!.manifest_sha256!, confirm: confirmed }), onSuccess: data => { save(data); setReview(null); setConfirmed(false); } });
  if (me.role !== "admin") return <p>Release updates are managed by admins.</p>;
  const data = query.data;
  const release = data?.release;
  const busy = !!data?.busy || apply.isPending;
  const eligible = !!(data?.enabled && data.installer_ready && data.backup_ready && release?.newer && release.verified && release.manifest_sha256 && !busy);
  const phase = String(operation.phase ?? "");
  return <>
    <div className="page-head"><h1>Release updates</h1></div>
    <div className="stack" style={{ maxWidth: 1000 }}>
      <SettingsForm me={me} section="updates" title="GitHub release updates" embedded intro="Check published stable Layer 8 releases when this appliance is online. Updates are never installed automatically." />
      <section className="card stack" aria-label="Release status">
        {query.isPending && <p>Reading installed version…</p>}
        {query.error && <p role="alert" className="error">{busy ? "The appliance may be in maintenance. This page keeps checking; wait for it to return." : query.error.message}</p>}
        {data && <>
          <div className="row-gap"><h2 className="spacer">Installed: {data.current_version}</h2><button className="btn2" type="button" disabled={!data.enabled || busy || check.isPending} onClick={() => check.mutate()}>{check.isPending ? "Checking GitHub…" : "Check GitHub for updates"}</button></div>
          <p className="muted small">Source: <a href={data.repository} target="_blank" rel="noreferrer">ddziubanski/layer8 releases</a>. Stable releases only; main and pull requests are never installed. Checks are cached for one minute.</p>
          {data.checked_at && <p className="small">Last checked {when(data.checked_at)}</p>}
          {!data.installer_ready && <p className="notice">Installation needs the Debian appliance updater service and a trusted publisher key provisioned locally. See docs/github-updates.md. Checking releases still works.</p>}
          {!data.backup_ready && <p className="notice">Set a backup passphrase under Settings → Backup before installing. Every update creates and verifies a separate encrypted local recovery backup.</p>}
          {data.problem && <p role="alert" className="error">{data.problem}</p>}
          {data.checked_at && !data.problem && !release && <p>No published stable releases were found.</p>}
          {release && <>
            <h3>{release.newer ? "Available" : "Latest published release"}: {release.version}</h3>
            {!release.newer && <p>This release is not newer than your installation. No downgrade will be offered.</p>}
            <a href={release.url} target="_blank" rel="noreferrer">Read release on GitHub</a>
            <div className="body-text small">{release.notes || "No release notes supplied."}</div>
            {release.problem && <p role="alert" className="error">{release.problem}</p>}
            {release.verified && <p>Publisher signature verified{release.package_size ? ` · ${fileSize(release.package_size)}` : ""}. The installer verifies it again before changing anything.</p>}
            {eligible && <button className="btn" type="button" onClick={() => { setReview(release); setConfirmed(false); apply.reset(); }}>Review update</button>}
          </>}
          {!!phase && <div className="stack" aria-label="Update progress">
            <div role="status"><strong>{phase.replaceAll("_", " ")}</strong>{!!operation.step && <span> · Step {String(operation.step)} of {String(operation.steps)}</span>}{elapsed !== null && <span> · {Math.floor(elapsed / 60)}m {elapsed % 60}s elapsed</span>}<p>{String(operation.message ?? "")}</p></div>
            {phase === "downloading" && download && <label className="field">Downloaded {fileSize(download.received)} of {fileSize(download.total)}<progress aria-label="Update download" max={download.total} value={download.received} /></label>}
            {checks.length > 0 && <><h3>Verification checklist</h3><ul>{checks.map((item, index) => <li key={index}><strong>{item.state === "passed" ? "✓" : "✕"} {item.name.replaceAll("_", " ")}</strong> — {item.detail}</li>)}</ul></>}
            {events.length > 0 && <details><summary>Upgrade activity ({events.length})</summary><ol>{events.map((event, index) => <li key={index}><time>{when(event.at)}</time> · {event.message}</li>)}</ol></details>}
            {!!data.operation?.backup && <p className="small">Recovery backup: <code>{String(data.operation.backup)}</code></p>}
            {progressUrl && <p className="muted small">Progress continues through the web server while the API is stopped. Keep this tab open; refreshing the whole page during maintenance may show the maintenance notice.</p>}
          </div>}
          {busy && <p className="notice">An update is queued or maintenance is active. The API and worker will stop briefly; keep this page open. If the server rebooted or recovery is required, inspect the appliance locally before retrying.</p>}
        </>}
      </section>
      {review && <section className="card stack" aria-label="Review release update">
        <h2>Install {review.version}</h2>
        <p>The installer verifies the reviewed package, stops API and worker access, makes a verified encrypted backup, installs offline dependencies, applies migrations and checks the application. Finish active work and choose a maintenance window first.</p>
        <p>If migration or application checks fail, it attempts recovery from the previous application and backup. An interrupted update or uncertain recovery keeps maintenance in place for local administrator recovery. Worker-start failures need local review because integrations may already have run.</p>
        <label className="inline"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />I have a maintenance window and accept the service interruption.</label>
        <div className="row-gap"><button className="btn" type="button" disabled={!confirmed || busy || !eligible || release?.manifest_sha256 !== review.manifest_sha256} onClick={() => apply.mutate()}>Back up and install {review.version}</button><button className="btn2" type="button" disabled={apply.isPending} onClick={() => setReview(null)}>Cancel</button></div>
      </section>}
      {(check.error || apply.error) && <p role="alert" className="error">{(check.error || apply.error)?.message}</p>}
    </div>
  </>;
}
