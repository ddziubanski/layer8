import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Me, type SoftwareDeployment as Deployment, type SoftwarePackage } from "../api/client";
import { can, uses } from "../permissions";

/** Shared ticket/device entry point. Changing target or account discards the previous review. */
export function SoftwareDeployment({ me, deviceId, ticketId, onChanged }: {
  me?: Me; deviceId: number; ticketId?: number; onChanged?: () => void;
}) {
  if (!me || !uses(me, "action1") || !can(me, "action1_deploy")) return null;
  return <DeploymentForm key={`${me.id}:${deviceId}:${ticketId ?? "device"}`} deviceId={deviceId} ticketId={ticketId} onChanged={onChanged} />;
}

function DeploymentForm({ deviceId, ticketId, onChanged }: { deviceId: number; ticketId?: number; onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<{ items: SoftwarePackage[]; more: boolean; offset: number } | null>(null);
  const [selected, setSelected] = useState("");
  const [version, setVersion] = useState("");
  const [review, setReview] = useState<Deployment | null>(null);
  const client = useQueryClient();
  const key = ["software-deployments", deviceId, ticketId];
  const history = useQuery({ queryKey: key, queryFn: () => api.action1.deployments(deviceId, ticketId), enabled: open, refetchInterval: open ? 30_000 : false });
  const changed = () => { client.invalidateQueries({ queryKey: key }); onChanged?.(); };
  const search = useMutation({ mutationFn: (offset: number) => api.action1.software(deviceId, query.trim(), offset, ticketId),
    onSuccess: result => { setPage(result); setSelected(""); setVersion(""); setReview(null); } });
  const preview = useMutation({ mutationFn: () => api.action1.preview({ device_id: deviceId, ticket_id: ticketId ?? null, package_id: selected, version_id: version }),
    onSuccess: setReview });
  const submit = useMutation({ mutationFn: (id: string) => api.action1.submit(id), onSuccess: result => { setReview(result); changed(); } });
  const refresh = useMutation({ mutationFn: (id: string) => api.action1.refreshDeployment(id), onSuccess: result => { if (review?.id === result.id) setReview(result); changed(); } });
  const busy = search.isPending || preview.isPending || submit.isPending || refresh.isPending;
  const error = search.error ?? preview.error ?? submit.error ?? refresh.error;
  const pkg = page?.items.find(p => p.id === selected);
  const resetReview = () => { setReview(null); preview.reset(); submit.reset(); };
  return <section className="stack" aria-label="Software deployment" style={{ gap: 8 }}>
    <button type="button" className="btn2" aria-expanded={open} disabled={busy} onClick={() => setOpen(!open)}>Install software through Action1…</button>
    {open && <>
      <p className="small muted">One application, one computer. Deployment is optional and needs separate credentials in Settings &gt; Action1.</p>
      {!review && <form className="stack" style={{ gap: 8 }} onSubmit={e => { e.preventDefault(); if (query.trim().length >= 2 && !busy) search.mutate(0); }}>
        <label className="field">Search Action1 software
          <input value={query} maxLength={100} disabled={busy} onChange={e => { setQuery(e.target.value); setPage(null); setSelected(""); setVersion(""); search.reset(); preview.reset(); }} />
        </label>
        <div><button type="submit" className="btn2" disabled={busy || query.trim().length < 2}>{search.isPending ? "Searching…" : "Search catalogue"}</button></div>
        {page && <>
          {page.items.length === 0 ? <p className="small">No deployable package versions in these results. Try another search.</p> : <>
            <label className="field">Application
              <select value={selected} disabled={busy} onChange={e => { setSelected(e.target.value); setVersion(""); preview.reset(); }}>
                <option value="">Choose an application…</option>
                {page.items.map(p => <option key={p.id} value={p.id}>{p.name} · {p.vendor} · {p.platform}</option>)}
              </select>
            </label>
            <label className="field">Version
              <select value={version} disabled={busy || !pkg} onChange={e => { setVersion(e.target.value); preview.reset(); }}>
                <option value="">Choose an exact version…</option>
                {pkg?.versions.map(v => <option key={v.id} value={v.id}>{v.version}</option>)}
              </select>
            </label>
            <div><button type="button" className="btn2" disabled={busy || !selected || !version} onClick={() => preview.mutate()}>Review deployment</button></div>
          </>}
          <div className="row-gap">
            {page.offset > 0 && <button type="button" className="linkish" disabled={busy} onClick={() => search.mutate(page.offset - 50)}>Previous results</button>}
            {page.more && <button type="button" className="linkish" disabled={busy} onClick={() => search.mutate(page.offset + 50)}>More results</button>}
          </div>
        </>}
      </form>}
      {review && <section className="stack small" aria-label="Deployment review" style={{ gap: 8 }}>
        <strong>{review.details.package_name} {review.details.version}</strong>
        <span>{review.details.vendor} · {review.details.platform}</span>
        <span>Computer: {review.details.device_name} ({review.details.endpoint_name})</span>
        <span>Organization: {review.details.organization} ({review.details.organization_id})</span>
        <span>Action1 endpoint: {review.details.endpoint_id} · {review.details.connection}</span>
        <p>Deploy now. Automatic reboot disabled. Action1 retries offline computers for up to 30 minutes. Packages with additional actions are not supported here.</p>
        <div><strong>Reported software matches (cached inventory)</strong>
          {review.details.installed.length ? <ul>{review.details.installed.map((i, n) => <li key={n}>{i.name} {i.version}</li>)}</ul> : <p>No matching software reported; this does not prove it is absent.</p>}
        </div>
        <p role="status">{review.message}</p>
        {review.state === "review" ? <div className="row-gap">
          <button type="button" className="btn" disabled={busy} onClick={() => submit.mutate(review.id)}>{submit.isPending ? "Submitting…" : "Deploy reviewed software"}</button>
          <button type="button" className="btn2" disabled={busy} onClick={resetReview}>Back to selection</button>
        </div> : <>
          <span>State: {review.state} · Reference: {review.id}</span>
          <button type="button" className="btn2" disabled={busy} onClick={() => refresh.mutate(review.id)}>Refresh deployment result</button>
          <button type="button" className="linkish" disabled={busy} onClick={resetReview}>Choose another application</button>
        </>}
      </section>}
      {error && <p role="alert" className="error small">{error.message}</p>}
      {history.isError && <p role="alert" className="error small">Deployment history could not be loaded. <button type="button" onClick={() => history.refetch()}>Retry history</button></p>}
      {!!history.data?.length && <details><summary>Recent deployments in this context</summary><ul className="small">
        {history.data.map(row => <li key={row.id}><button className="linkish" type="button" disabled={busy} onClick={() => { setReview(row); submit.reset(); refresh.reset(); }}>{row.details.package_name} {row.details.version} · {row.state}</button></li>)}
      </ul></details>}
    </>}
  </section>;
}
