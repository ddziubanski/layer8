// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api, type Me } from "../../api/client";
import type { components } from "../../api/schema";

type S = components["schemas"];
type Chosen = { package_id: string; version_id: string; name: string; version: string };
const RETRY: [string, string][] = [["30", "30 minutes"], ["480", "8 hours"], ["1440", "24 hours"]];
const STATE: Record<string, string> = { review: "Review", submitting: "Sending", unknown: "Unconfirmed", scheduled: "Scheduled", pending: "Pending",
  running: "Running", success: "Success", warning: "Warning", error: "Error", stopped: "Stopped" };

function stateClass(state: string) {
  return state === "success" ? "pill ok" : ["error", "unknown", "stopped"].includes(state) ? "pill bad" : "pill";
}

function describe(b: S["SoftwareBatchOut"]) {
  const t = b.targets[0];
  const where = t?.type === "EndpointGroup" ? `group ${t.name} (${b.member_count})` : b.targets.map(x => x.name).join(", ");
  return { what: b.packages.map(p => `${p.package_name} ${p.version}`).join(", "), where };
}

/** Devices > Deploy software: several machines or an Action1 group, packages or a bundle, now or once later. */
export function DeploySoftware({ me }: { me: Me }) {
  const client = useQueryClient();
  const isAdmin = me.role === "admin";
  const orgs = useQuery({ queryKey: ["action1-deploy-orgs"], queryFn: api.action1.deployOrganizations, retry: false });
  const [org, setOrg] = useState("");
  useEffect(() => { if (!org && orgs.data?.length === 1) setOrg(orgs.data[0].id); }, [orgs.data, org]);
  const [mode, setMode] = useState<"machines" | "group">("machines");
  const [picked, setPicked] = useState<number[]>([]);
  const [filter, setFilter] = useState("");
  const [group, setGroup] = useState("");
  const [platform, setPlatform] = useState("Windows");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<Chosen[]>([]);
  const [bundle, setBundle] = useState("");
  const [bundleName, setBundleName] = useState("");
  const [later, setLater] = useState(false);
  const [at, setAt] = useState("");
  const [retry, setRetry] = useState("30");
  const [ticket, setTicket] = useState("");
  const [review, setReview] = useState<S["SoftwareBatchOut"] | null>(null);
  const [confirm, setConfirm] = useState(false);

  const machines = useQuery({ queryKey: ["action1-deploy-machines", org], queryFn: () => api.action1.deployMachines(org), enabled: !!org && mode === "machines" });
  const groups = useQuery({ queryKey: ["action1-deploy-groups", org], queryFn: () => api.action1.deployGroups(org), enabled: !!org && mode === "group" });
  const bundles = useQuery({ queryKey: ["action1-bundles", org], queryFn: () => api.action1.bundles(org), enabled: !!org });
  const catalog = useQuery({ queryKey: ["action1-deploy-software", org, search, platform], queryFn: () => api.action1.deploySoftware(org, search, platform),
    enabled: !!org && search.length >= 2 });
  const runs = useQuery({ queryKey: ["action1-batches"], queryFn: api.action1.batches,
    refetchInterval: query => (query.state.data?.some(b => ["submitting", "pending", "running", "scheduled"].includes(b.state)) ? 30000 : false) });

  const changed = () => { setReview(null); setConfirm(false); };
  const shown = useMemo(() => (machines.data ?? []).filter(m => !filter || m.name.toLowerCase().includes(filter.toLowerCase())), [machines.data, filter]);
  const selectedBundle = bundles.data?.find(b => String(b.id) === bundle);

  const previewM = useMutation({
    mutationFn: () => api.action1.batchPreview({
      organization_id: org, device_ids: mode === "machines" ? picked : [], group_id: mode === "group" ? group : null,
      packages: selectedBundle ? [] : chosen.map(c => ({ package_id: c.package_id, version_id: c.version_id })), bundle_id: selectedBundle?.id ?? null,
      ticket_id: ticket ? Number(ticket) : null, run_at: later && at ? new Date(at).toISOString() : null, retry_minutes: retry,
    }),
    onSuccess: data => { setReview(data); setConfirm(false); },
  });
  const submitM = useMutation({
    mutationFn: () => api.action1.batchSubmit(review!.id),
    onSuccess: data => { setReview(null); setConfirm(false); setPicked([]); client.setQueryData(["action1-batches"], (old: S["SoftwareBatchOut"][] | undefined) => [data, ...(old ?? []).filter(b => b.id !== data.id)]); },
  });
  const refreshM = useMutation({ mutationFn: (id: string) => api.action1.batchRefresh(id), onSuccess: () => client.invalidateQueries({ queryKey: ["action1-batches"] }) });
  const saveBundle = useMutation({
    mutationFn: () => api.action1.saveBundle({ organization_id: org, name: bundleName, packages: chosen.map(c => ({ package_id: c.package_id, version_id: c.version_id })) }),
    onSuccess: b => { setBundleName(""); client.invalidateQueries({ queryKey: ["action1-bundles", org] }); setBundle(String(b.id)); setChosen([]); },
  });
  const deleteBundle = useMutation({ mutationFn: (id: number) => api.action1.deleteBundle(id), onSuccess: () => { setBundle(""); client.invalidateQueries({ queryKey: ["action1-bundles", org] }); } });

  if (orgs.error) return <p className="error" role="alert">{orgs.error.message}</p>;
  if (orgs.data && !orgs.data.length) return <p>No Action1 organization is allowed for deployment. An admin sets that in <Link to="/settings/action1">Settings &gt; Action1</Link>.</p>;
  const hasTargets = mode === "machines" ? picked.length > 0 : !!group;
  const hasPackages = !!selectedBundle || chosen.length > 0;
  const busy = previewM.isPending || submitM.isPending;

  return <div className="stack">
    <section className="card stack" aria-label="Deploy software through Action1">
      <h2>Deploy software through Action1</h2>
      <p className="muted small">Choose machines or an Action1 group, the software, and when. Review checks everything with Action1 and sends nothing; <strong>Deploy</strong> is the one step that does. Machines aren't restarted automatically.</p>
      {(orgs.data?.length ?? 0) > 1 && <label className="field">Organization<select value={org} onChange={e => { setOrg(e.target.value); setPicked([]); setGroup(""); setChosen([]); setBundle(""); changed(); }}>
        <option value="">Choose…</option>{orgs.data!.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></label>}

      {org && <>
        <h3>Where</h3>
        <div className="row-actions" role="radiogroup" aria-label="Target">
          <label className="inline"><input type="radio" checked={mode === "machines"} onChange={() => { setMode("machines"); changed(); }} />Machines</label>
          <label className="inline"><input type="radio" checked={mode === "group"} onChange={() => { setMode("group"); changed(); }} />An Action1 group</label>
        </div>
        {mode === "machines" ? <>
          <input aria-label="Filter machines" placeholder="Filter machines" value={filter} onChange={e => setFilter(e.target.value)} />
          <div className="deploy-machines" role="group" aria-label="Machines">
            {shown.map(m => <label key={m.device_id} className="inline"><input type="checkbox" checked={picked.includes(m.device_id)}
              disabled={!picked.includes(m.device_id) && picked.length >= 50}
              onChange={e => { changed(); setPicked(p => e.target.checked ? [...p, m.device_id] : p.filter(x => x !== m.device_id)); }} />
              {m.name} <span className="muted small">{m.os}</span></label>)}
            {machines.data && !shown.length && <p className="muted small">No machines matched to Action1 in this organization.</p>}
          </div>
          <p className="muted small">{picked.length} chosen (up to 50).</p>
        </> : <label className="field">Action1 group<select value={group} onChange={e => { setGroup(e.target.value); changed(); }}>
          <option value="">Choose a group</option>{groups.data?.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>}

        <h3>What</h3>
        {!!bundles.data?.length && <div className="row-actions">
          <label className="field">Bundle<select value={bundle} onChange={e => { setBundle(e.target.value); changed(); }}>
            <option value="">No bundle: choose packages</option>{bundles.data.map(b => <option key={b.id} value={b.id}>{b.name} ({b.packages.length})</option>)}</select></label>
          {selectedBundle && isAdmin && <button className="btn2 small" type="button" onClick={() => deleteBundle.mutate(selectedBundle.id)}>Delete bundle</button>}
        </div>}
        {selectedBundle ? <ul>{selectedBundle.packages.map(p => <li key={String(p.package_id)}>{String(p.package_name)} {String(p.version)}</li>)}</ul> : <>
          <div className="row-actions">
            <select aria-label="Platform" value={platform} onChange={e => { setPlatform(e.target.value); setChosen([]); changed(); }}><option>Windows</option><option>Mac</option></select>
            <input aria-label="Search software" placeholder="Search Action1's software repository" value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") setSearch(q.trim()); }} />
            <button className="btn2" type="button" disabled={q.trim().length < 2} onClick={() => setSearch(q.trim())}>Search</button>
          </div>
          {catalog.error && <p className="error" role="alert">{catalog.error.message}</p>}
          {catalog.data?.items.map(p => <div key={p.id} className="row-actions">
            <span>{p.name} <span className="muted small">{p.vendor}</span></span>
            <select aria-label={`Version of ${p.name}`} id={`v-${p.id}`} defaultValue={p.versions[0]?.id}>{p.versions.map(v => <option key={v.id} value={v.id}>{v.version}</option>)}</select>
            <button className="btn2 small" type="button" disabled={chosen.some(c => c.package_id === p.id) || chosen.length >= 10} onClick={() => {
              const select = document.getElementById(`v-${p.id}`) as HTMLSelectElement | null;
              const v = p.versions.find(x => x.id === select?.value) ?? p.versions[0];
              setChosen(c => [...c, { package_id: p.id, version_id: v.id, name: p.name, version: v.version }]); changed();
            }}>Add</button>
          </div>)}
          {!!chosen.length && <ul aria-label="Chosen packages">{chosen.map(c => <li key={c.package_id}>{c.name} {c.version} <button className="btn2 small" type="button"
            onClick={() => { setChosen(x => x.filter(y => y.package_id !== c.package_id)); changed(); }}>Remove</button></li>)}</ul>}
          {isAdmin && chosen.length > 0 && <div className="row-actions">
            <input aria-label="Bundle name" placeholder="Save these as a bundle, e.g. New starter" value={bundleName} onChange={e => setBundleName(e.target.value)} />
            <button className="btn2 small" type="button" disabled={!bundleName.trim() || saveBundle.isPending} onClick={() => saveBundle.mutate()}>Save bundle</button>
          </div>}
          {saveBundle.error && <p className="error" role="alert">{saveBundle.error.message}</p>}
        </>}

        <h3>When</h3>
        <div className="row-actions" role="radiogroup" aria-label="When">
          <label className="inline"><input type="radio" checked={!later} onChange={() => { setLater(false); changed(); }} />Now</label>
          <label className="inline"><input type="radio" checked={later} onChange={() => { setLater(true); changed(); }} />Once, at</label>
          <input type="datetime-local" aria-label="Date and time" disabled={!later} value={at} onChange={e => { setAt(e.target.value); changed(); }} />
        </div>
        <label className="field">Keep trying offline machines for<select value={retry} onChange={e => { setRetry(e.target.value); changed(); }}>
          {RETRY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label className="field">Ticket (optional: notes go on it)<input inputMode="numeric" value={ticket} onChange={e => { setTicket(e.target.value.replace(/\D/g, "")); changed(); }} /></label>

        <div><button className="btn2" type="button" disabled={busy || !hasTargets || !hasPackages || (later && !at)} onClick={() => previewM.mutate()}>
          {previewM.isPending ? "Checking with Action1…" : "Review"}</button></div>
        {(previewM.error || submitM.error) && <p className="error" role="alert">{previewM.error?.message || submitM.error?.message}</p>}
      </>}
    </section>

    {review && <section className="card stack" aria-label="Deployment review">
      <h2>Review</h2>
      <dl className="import-counts">
        <div><dt>Organization</dt><dd className="small">{review.organization}</dd></div>
        <div><dt>Machines</dt><dd>{review.member_count}</dd></div>
        <div><dt>Packages</dt><dd>{review.packages.length}</dd></div>
        <div><dt>When</dt><dd className="small">{review.run_at ? new Date(review.run_at).toLocaleString() : "Now"}</dd></div>
      </dl>
      <p><strong>{describe(review).what}</strong> to {describe(review).where}. Offline machines are retried for {RETRY.find(r => r[0] === review.retry_minutes)?.[1]}.</p>
      <details><summary>Machines ({review.member_count})</summary><ul>{review.members.map((m, i) => <li key={i}>{String(m.name)} <span className="muted small">{String(m.connection)}</span></li>)}</ul>
        {review.member_count > review.members.length && <p className="muted small">and {review.member_count - review.members.length} more.</p>}</details>
      <p className="muted small">{review.message} The review is valid for 10 minutes.</p>
      <label className="inline"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />I've reviewed the machines, packages and licences. Send this to Action1.</label>
      <div><button className="btn" type="button" disabled={!confirm || busy} onClick={() => submitM.mutate()}>{review.run_at ? "Schedule in Action1" : "Deploy"}</button></div>
    </section>}

    <section className="card stack" aria-label="Recent deployments">
      <h2>Recent deployments</h2>
      {!runs.data?.length && <p className="muted small">None yet.</p>}
      {runs.data?.map(b => { const d = describe(b); return <div key={b.id} className="stack deploy-run">
        <div className="row-actions"><span className={stateClass(b.state)}>{STATE[b.state] ?? b.state}</span><strong>{d.what}</strong><span>to {d.where}</span>
          {b.run_at && <span className="muted small">at {new Date(b.run_at).toLocaleString()}</span>}
          {b.ticket_id && <Link to={`/tickets/${b.ticket_id}`}>ticket</Link>}
          {["submitting", "unknown", "pending", "running", "scheduled"].includes(b.state) && <button className="btn2 small" type="button" disabled={refreshM.isPending} onClick={() => refreshM.mutate(b.id)}>Refresh</button>}</div>
        <p className="muted small">{b.message}</p>
        {!!b.results.length && <details><summary>Per machine ({b.results.length})</summary><table className="table"><thead><tr><th>Machine</th><th>Result</th><th>When</th><th>Details</th></tr></thead>
          <tbody>{b.results.map((r, i) => <tr key={i}><td>{String(r.endpoint)}</td><td>{String(r.status)}</td><td>{String(r.time).slice(0, 16).replace("T", " ")}</td><td>{String(r.details)}</td></tr>)}</tbody></table></details>}
      </div>; })}
    </section>
  </div>;
}
