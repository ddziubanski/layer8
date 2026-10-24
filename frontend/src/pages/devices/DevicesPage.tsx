// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { api, type DeviceDetail, type DeviceIn, type Me } from "../../api/client";
import { StatusChip } from "../../components/Chips";
import { DeviceIps } from "../../components/DeviceIps";
import { PatchStatus } from "../../components/PatchStatus";
import { SoftwareDeployment } from "../../components/SoftwareDeployment";
import { UserPicker } from "../../components/UserPicker";
import { when } from "../../format";
import { can, uses } from "../../permissions";

const PAGE = 50;

/** Devices: AD computers (from directory sync) and devices added by hand. */
export function DevicesPage({ me }: { me: Me }) {
  const [params] = useSearchParams();
  const view = params.get("view") ?? "all";
  const tabs = (
    <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
      {[
        ["all", "All devices"],
        ["stale", "Stale"],
        ["unknown", "Unknown on the network"],
        ["lifecycle", "Lifecycle"],
      ].map(([key, label]) => (
        <Link key={key} to={key === "all" ? "/devices" : `/devices?view=${key}`} role="tab" aria-selected={view === key} className={view === key ? "active" : undefined}>
          {label}
        </Link>
      ))}
    </div>
  );
  if (view === "lifecycle")
    return (
      <>
        <div className="page-head row-gap">
          <WorkspaceTitle title="Devices" area="ASSET OPERATIONS" />
        </div>
        {tabs}
        <LifecycleView />
      </>
    );
  if (view === "stale" || view === "unknown")
    return (
      <>
        <div className="page-head row-gap">
          <WorkspaceTitle title="Devices" area="ASSET OPERATIONS" />
        </div>
        {tabs}
        <Sweeps view={view} action1={uses(me, "action1")} />
      </>
    );
  return <AllDevices me={me} tabs={tabs} />;
}

function AllDevices({ me, tabs }: { me: Me; tabs: ReactNode }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [inactive, setInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(q.trim());
      setOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);
  const page = useQuery({ queryKey: ["devices", debounced, inactive, offset], queryFn: () => api.devices.list({ q: debounced, include_inactive: inactive, limit: PAGE, offset }) });
  const total = page.data?.total ?? 0;

  return (
    <>
      <div className="page-head row-gap">
        <WorkspaceTitle title="Devices" area="ASSET OPERATIONS" />
        {can(me, "devices_add") && (
          <button className="btn2" type="button" aria-expanded={adding} onClick={() => setAdding(!adding)}>
            Add a device
          </button>
        )}
      </div>
      {tabs}
      <div className="stack">
        {adding && <AddDevice onDone={() => setAdding(false)} />}
        <div className="row-gap">
          <input className="spacer" style={{ maxWidth: 420 }} type="search" placeholder="Search name, OS, description or person" aria-label="Search devices" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="inline small">
            <input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /> Include ones gone from AD
          </label>
          <span className="muted small">{total} device(s)</span>
        </div>
        {page.error && <p className="error">{page.error.message}</p>}
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Name</th>
                <th>Operating system</th>
                <th>Assigned to</th>
                <th>Last logon</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {page.data?.items.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/devices/${d.id}`}>{d.name}</Link>
                    {!d.enabled && <span className="chip n"> Disabled</span>}
                    {!d.is_active && <span className="chip a"> Gone from AD</span>}
                  </td>
                  <td>{[d.os, d.os_version].filter(Boolean).join(" ") || "–"}</td>
                  <td>{d.assigned_user?.display_name ?? "–"}</td>
                  <td>{d.last_logon_at ? when(d.last_logon_at) : "–"}</td>
                  <td className="muted small">{d.source === "directory" ? "AD" : "Added by hand"}</td>
                </tr>
              ))}
              {page.data?.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    {debounced ? "No device matches." : "No devices yet. AD computers arrive with the next directory sync (Settings > Directory)."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {total > PAGE && (
          <div className="row-gap">
            <button className="btn2" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
            </span>
            <button className="btn2" type="button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        )}
      </div>
    </>
  );
}

const day = (iso: string | null | undefined) => (iso ? new Date(iso + "T00:00").toLocaleDateString() : "–");

/** Devices > Lifecycle: warranties ending or ended, replacements due, and how many come due each quarter. */
function LifecycleView() {
  const r = useQuery({ queryKey: ["device-lifecycle"], queryFn: api.devices.lifecycle });
  if (r.error) return <p className="error">{r.error.message}</p>;
  const l = r.data;
  if (!l) return <p className="muted">Loading…</p>;
  const most = Math.max(1, ...l.forecast.map(([, n]) => n));
  const list = (rows: { id: number; name: string; warranty_ends?: string | null; model?: string; assigned_user?: { display_name: string } | null }[], empty: string) =>
    rows.length === 0 ? (
      <span className="muted small">{empty}</span>
    ) : (
      <ul className="checklist-items small">
        {rows.map((d) => (
          <li key={d.id}>
            <Link to={`/devices/${d.id}`}>{d.name}</Link>
            <span className="muted">
              {d.model && ` · ${d.model}`} · warranty {day(d.warranty_ends)}
              {d.assigned_user && ` · ${d.assigned_user.display_name}`}
            </span>
          </li>
        ))}
      </ul>
    );
  return (
    <div className="stack">
      <p className="muted small" style={{ margin: 0 }}>
        Devices are due for replacement {l.life_years} years after purchase unless they have their own date (Settings &gt; Device sweeps). Set the dates on each device&apos;s page.
        {l.unknown > 0 && ` ${l.unknown} device(s) have no purchase or replace-by date yet.`}
      </p>
      <dl className="device-lifecycle-summary" aria-label="Lifecycle summary">
        {[["Warranty ending soon", l.warranty_soon.length], ["Warranty ended", l.warranty_ended.length], ["Replacement overdue", l.overdue], ["Missing purchase / replacement dates", l.unknown]].map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
      </dl>
      <div className="two-col">
        <section className="card stack" style={{ margin: 0, gap: 6 }} aria-label="Warranty ending">
          <div className="lbl" style={{ margin: 0 }}>
            Warranty ends in the next {l.warn_days} days
          </div>
          {list(l.warranty_soon, "None.")}
          <div className="lbl" style={{ margin: "8px 0 0" }}>
            Warranty ended (still in use)
          </div>
          {list(l.warranty_ended, "None.")}
        </section>
        <section className="card stack" style={{ margin: 0, gap: 6 }} aria-label="Replacement forecast">
          <div className="lbl" style={{ margin: 0 }}>
            Due for replacement, by quarter
          </div>
          {l.overdue > 0 && <span className="small error">{l.overdue} overdue (due before this quarter)</span>}
          <table className="grid report-table">
            <tbody>
              {l.forecast.map(([q, n]) => (
                <tr key={q}>
                  <td className="small">{q}</td>
                  <td className="bar-cell">
                    <span className="bar-value small">{n}</span>
                    <span className="bar-track">{n > 0 && <span className="bar" style={{ width: `${(n / most) * 100}%` }} />}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
      <section className="card stack" style={{ margin: 0, gap: 6 }} aria-label="Due within a year">
        <div className="lbl" style={{ margin: 0 }}>
          Due within a year (or overdue)
        </div>
        {l.replace_soon.length === 0 ? (
          <span className="muted small">None.</span>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Device</th>
                <th>Due</th>
                <th>Model</th>
                <th>Bought</th>
                <th>Assigned to</th>
              </tr>
            </thead>
            <tbody>
              {l.replace_soon.map(({ device: d, replace_due }) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/devices/${d.id}`}>{d.name}</Link>
                  </td>
                  <td className={replace_due && new Date(replace_due) < new Date() ? "error" : undefined}>{day(replace_due)}</td>
                  <td className="small">{[d.manufacturer, d.model].filter(Boolean).join(" ") || "–"}</td>
                  <td className="small">{day(d.purchased_on)}</td>
                  <td className="small">{d.assigned_user?.display_name ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

/** A device's make, model, serial and dates: technicians keep them (Action1 fills make and serial when empty). */
function LifecycleCard({ device: d, onSaved }: { device: DeviceDetail; onSaved: () => void }) {
  const [f, setF] = useState({
    manufacturer: d.manufacturer ?? "", model: d.model ?? "", serial: d.serial ?? "",
    purchased_on: d.purchased_on ?? "", warranty_ends: d.warranty_ends ?? "", replace_by: d.replace_by ?? "",
  });
  const save = useMutation({
    mutationFn: () =>
      api.devices.update(d.id, { ...f, purchased_on: f.purchased_on || null, warranty_ends: f.warranty_ends || null, replace_by: f.replace_by || null }),
    onSuccess: onSaved,
  });
  const field = (key: keyof typeof f, label: string, type = "text") => (
    <label className="field">
      {label}
      <input type={type} value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} />
    </label>
  );
  return (
    <form
      className="card stack"
      style={{ gap: 8 }}
      aria-label="Lifecycle"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="lbl" style={{ margin: 0 }}>
        Lifecycle
      </div>
      <div className="form-grid">
        {field("manufacturer", "Manufacturer")}
        {field("model", "Model")}
        {field("serial", "Serial number")}
        {field("purchased_on", "Bought", "date")}
        {field("warranty_ends", "Warranty ends", "date")}
        {field("replace_by", "Replace by (empty: from Settings)", "date")}
      </div>
      {save.error && <p className="error small">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn2" type="submit" disabled={save.isPending}>
          Save
        </button>
        {save.isSuccess && <span className="muted small">Saved.</span>}
      </div>
    </form>
  );
}

const MAKER: Record<string, string> = { dell: "Dell", lenovo: "Lenovo" };

/** What Dell or Lenovo says about the warranty (their APIs, Settings > Warranty lookups): look up now, take their dates. */
function MakerWarranty({ device: d, onChanged }: { device: DeviceDetail; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["device-warranty", d.id];
  const w = useQuery({ queryKey: key, queryFn: () => api.devices.warranty(d.id) });
  const done = (data: unknown) => (queryClient.setQueryData(key, data), onChanged());
  const lookup = useMutation({ mutationFn: () => api.devices.lookupWarranty(d.id), onSuccess: done });
  const apply = useMutation({ mutationFn: () => api.devices.applyWarranty(d.id), onSuccess: done });
  const x = w.data;
  if (!x || (!x.vendor && !x.checked_at)) return null;
  const maker = MAKER[x.vendor ?? ""] ?? "The maker";
  return (
    <section className="card stack" aria-label="Warranty from the maker" style={{ gap: 8 }}>
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          Warranty from {maker}
        </div>
        {x.available && (
          <button className="btn2" type="button" disabled={lookup.isPending} onClick={() => lookup.mutate()}>
            {lookup.isPending ? "Looking up…" : "Look up now"}
          </button>
        )}
      </div>
      {!x.available && <p className="muted small" style={{ margin: 0 }}>{x.reason}</p>}
      {x.checked_at && x.error && (
        <p className="small" style={{ margin: 0 }}>
          {x.error} <span className="muted">({when(x.checked_at)}, serial {x.serial})</span>
        </p>
      )}
      {x.checked_at && !x.error && (
        <>
          <p className="small" style={{ margin: 0 }}>
            {x.product && <strong>{x.product}</strong>} {x.warranty_ends && <>· covered until <strong>{x.warranty_ends}</strong></>}
            {x.shipped_on && <> · shipped {x.shipped_on}</>}
            {x.purchased_on && <> · bought {x.purchased_on}</>} <span className="muted">(asked {when(x.checked_at)})</span>
          </p>
          {x.entitlements.length > 0 && (
            <table className="grid">
              <thead>
                <tr>
                  <th>Cover</th>
                  <th>Type</th>
                  <th>From</th>
                  <th>Until</th>
                </tr>
              </thead>
              <tbody>
                {x.entitlements.map((e, i) => (
                  <tr key={i}>
                    <td>{e.name}</td>
                    <td className="small">{e.type}</td>
                    <td className="small">{e.start ?? ""}</td>
                    <td className="small">{e.end ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {x.differs && (
            <div className="row-gap">
              <span className="small">
                {maker}'s dates differ from this device's (warranty {d.warranty_ends ?? "not set"}, bought {d.purchased_on ?? "not set"}).
              </span>
              <button className="btn2" type="button" disabled={apply.isPending} onClick={() => apply.mutate()}>
                Use {maker}'s dates
              </button>
            </div>
          )}
        </>
      )}
      {!x.checked_at && x.available && <p className="muted small" style={{ margin: 0 }}>Not looked up yet: it will be within a few hours, or look it up now.</p>}
      {(lookup.error ?? apply.error) && <p className="error small">{(lookup.error ?? apply.error)!.message}</p>}
    </section>
  );
}

const KIND_NOTE: Record<string, string> = { address: "Seen by scans", undocumented: "Seen by a scan, not in IPAM", action1: "In Action1" };

/** Sweeps: devices with no sign of life (AD, Action1, scans) for a while, and things seen that aren't devices here. Flags only. */
function Sweeps({ view, action1 }: { view: "stale" | "unknown"; action1: boolean }) {
  const sweep = useQuery({ queryKey: ["device-sweep"], queryFn: () => api.devices.sweep() });
  const s = sweep.data;
  if (sweep.error) return <p className="error">{sweep.error.message}</p>;
  if (!s) return <p className="muted">Checking…</p>;
  if (view === "stale")
    return (
      <div className="stack">
        <p className="muted small" style={{ margin: 0 }}>
          Active devices with no sign of life for {s.stale_days} days: not in AD&apos;s last logon (up to ~14 days behind), Action1 or network scans. Nothing is changed here;
          disable or retire them in AD. The number of days is in Settings &gt; Device sweeps.
        </p>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Device</th>
                <th>Last seen</th>
                <th>AD logon</th>
                {action1 && <th>Action1</th>}
                <th>Network</th>
                <th>Assigned to</th>
              </tr>
            </thead>
            <tbody>
              {s.stale.map((x) => (
                <tr key={x.device.id}>
                  <td>
                    <Link to={`/devices/${x.device.id}`}>{x.device.name}</Link>
                    {!x.device.enabled && <span className="chip n"> Disabled</span>}
                  </td>
                  <td>{x.last_seen_at ? when(x.last_seen_at) : <span className="muted">never</span>}</td>
                  <td className="small">{x.ad_logon_at ? when(x.ad_logon_at) : "–"}</td>
                  {action1 && <td className="small">{x.action1_at ? when(x.action1_at) : "–"}</td>}
                  <td className="small">{x.network_at ? when(x.network_at) : "–"}</td>
                  <td className="small">{x.device.assigned_user?.display_name ?? "–"}</td>
                </tr>
              ))}
              {s.stale.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    Nothing stale.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  return (
    <div className="stack">
      <p className="muted small" style={{ margin: 0 }}>
        Seen in the last {s.recent_days} days but not a device here: addresses scans saw that aren&apos;t tied to a device or system (and whose host name matches none),
        scan findings not dealt with, and machines in Action1 without a device. Document them in IP addresses, or add the device.
      </p>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th>MAC</th>
              <th>Last seen</th>
              <th>Where from</th>
            </tr>
          </thead>
          <tbody>
            {s.unknown.map((u, i) => (
              <tr key={i}>
                <td>{u.name}</td>
                <td className="mono">{u.address ? <Link to={`/ipam/addresses?q=${encodeURIComponent(u.address)}`}>{u.address}</Link> : "–"}</td>
                <td className="mono small">{u.mac || "–"}</td>
                <td className="small">{u.last_seen_at ? when(u.last_seen_at) : "–"}</td>
                <td className="small">
                  {KIND_NOTE[u.kind]}
                  {u.notes.length > 0 && <span className="muted"> · {u.notes.join(", ")}</span>}
                </td>
              </tr>
            ))}
            {s.unknown.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  Nothing unknown.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AddDevice({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [d, setD] = useState<DeviceIn>({ name: "", dns_hostname: "", os: "", description: "", notes: "", assigned_user_id: null });
  const [who, setWho] = useState<{ id: number; display_name: string; email: string } | null>(null);
  const create = useMutation({
    mutationFn: () => api.devices.create({ ...d, dns_hostname: d.dns_hostname || null, os: d.os || null, description: d.description || null, assigned_user_id: who?.id ?? null }),
    onSuccess: (device) => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      onDone();
      navigate(`/devices/${device.id}`);
    },
  });
  return (
    <form
      className="card stack settings-form"
      aria-label="Add a device"
      style={{ margin: 0 }}
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="lbl">Add a device</div>
      <p className="muted small" style={{ margin: 0 }}>
        For kit that isn't in AD: switches, printers, non-domain PCs. AD computers are added by directory sync.
      </p>
      <label className="field">
        Name
        <input required maxLength={255} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
      </label>
      <label className="field">
        Host name <span className="muted small">(optional; how scripts reach it)</span>
        <input maxLength={255} value={d.dns_hostname ?? ""} onChange={(e) => setD({ ...d, dns_hostname: e.target.value })} />
      </label>
      <label className="field">
        Operating system <span className="muted small">(optional)</span>
        <input maxLength={255} value={d.os ?? ""} onChange={(e) => setD({ ...d, os: e.target.value })} />
      </label>
      <UserPicker label="Assigned to (optional)" value={who} onChange={setWho} />
      <label className="field">
        Description <span className="muted small">(optional)</span>
        <input value={d.description ?? ""} onChange={(e) => setD({ ...d, description: e.target.value })} />
      </label>
      {create.error && <p className="error">{create.error.message}</p>}
      <div className="row-gap">
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
        <button className="btn" type="submit" disabled={create.isPending}>
          Add device
        </button>
      </div>
    </form>
  );
}

/** One device: its details, notes, and the tickets it's linked to. */
export function DevicePage({ me }: { me: Me }) {
  const { deviceId } = useParams();
  const id = Number(deviceId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const device = useQuery({ queryKey: ["device", id], queryFn: () => api.devices.get(id) });
  const [notes, setNotes] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => api.devices.update(id, { notes: notes ?? "" }),
    onSuccess: () => {
      setNotes(null);
      queryClient.invalidateQueries({ queryKey: ["device", id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.devices.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      navigate("/devices");
    },
  });
  if (device.error) return <p className="error">{device.error.message}</p>;
  const d = device.data;
  if (!d) return <p className="muted">Loading…</p>;
  const facts: [string, string | null | undefined][] = [
    ["Host name", d.dns_hostname],
    ["Operating system", [d.os, d.os_version].filter(Boolean).join(" ")],
    ["Assigned to", d.assigned_user ? `${d.assigned_user.display_name} (${d.assigned_user.email})` : null],
    ["Last logon", d.last_logon_at ? `${new Date(d.last_logon_at).toLocaleString()} (AD updates this every ~2 weeks)` : null],
    ["Description", d.description],
    ["Source", d.source === "directory" ? `AD: ${d.directory_dn}` : "Added by hand"],
    ["State", [d.enabled ? "Enabled" : "Disabled in AD", d.is_active ? null : "gone from AD"].filter(Boolean).join(", ")],
  ];
  return (
    <>
      <header className="device-workspace-head">
        <div className="spacer">
          <Link className="small" to="/devices">All devices</Link>
          <p className="workspace-area">ASSET WORKSPACE</p>
          <h1>{d.name}</h1>
          <p className="muted">{[d.manufacturer, d.model, d.dns_hostname].filter(Boolean).join(" · ") || "Device inventory and service history"}</p>
          <div className="row-gap device-state" aria-label="Device state">
            <span className="chip n">{d.source === "directory" ? "Directory managed" : "Manually added"}</span>
            <span className={`chip ${d.is_active ? "g" : "a"}`}>{d.is_active ? "Active inventory" : "Inactive inventory"}</span>
            {!d.enabled && <span className="chip a">Disabled</span>}
          </div>
        </div>
        <a className="btn2" href="#device-notes">Go to notes</a>
      </header>
      <div className="device-workspace-grid">
        <div className="stack device-workspace-primary">
          <section className="card device-facts" aria-label="Details">
            <h2>Identity and ownership</h2>
            <dl className="facts">
              {facts
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k}>
                    <dt className="muted small">{k}</dt>
                    <dd style={{ margin: "0 0 8px" }}>{v}</dd>
                  </div>
                ))}
            </dl>
          </section>
          <LifecycleCard key={`${d.warranty_ends}-${d.purchased_on}-${d.model}`} device={d} onSaved={() => queryClient.invalidateQueries({ queryKey: ["device", id] })} />
          <MakerWarranty device={d} onChanged={() => queryClient.invalidateQueries({ queryKey: ["device", id] })} />
          <section id="device-notes" tabIndex={-1} className="card stack" aria-label="Notes" style={{ gap: 8 }}>
            <label className="field">
              Notes <span className="muted small">(for the service desk)</span>
              <textarea rows={3} value={notes ?? d.notes} onChange={(e) => setNotes(e.target.value)} />
            </label>
            {notes !== null && notes !== d.notes && (
              <div className="row-gap">
                <button className="btn" type="button" disabled={save.isPending} onClick={() => save.mutate()}>
                  Save notes
                </button>
                <button className="btn2" type="button" onClick={() => setNotes(null)}>
                  Undo
                </button>
              </div>
            )}
            {save.error && <p className="error">{save.error.message}</p>}
          </section>
          {can(me, "devices_delete") && d.source === "manual" && (
            <div>
              <button
                className="linkish small"
                type="button"
                onClick={() => {
                  if (window.confirm(`Remove ${d.name}? Its links to tickets go too.`)) remove.mutate();
                }}
              >
                Remove this device
              </button>
              {remove.error && <p className="error">{remove.error.message}</p>}
            </div>
          )}
        </div>
        <aside className="stack device-workspace-context" aria-label="Device service context">
          {uses(me, "action1") && (
            <section className="card stack" aria-label="Patching" style={{ gap: 8 }}>
              <div className="lbl" style={{ margin: 0 }}>
                Patching (Action1)
              </div>
              <PatchStatus deviceId={d.id} />
              <SoftwareDeployment me={me} deviceId={d.id} />
            </section>
          )}
          <section className="card stack" aria-label="IP addresses" style={{ gap: 8 }}>
            <div className="lbl" style={{ margin: 0 }}>
              IP addresses
            </div>
            <DeviceIps deviceId={d.id} />
          </section>
          <section className="card stack" aria-label="Tickets" style={{ gap: 8 }}>
            <div className="lbl" style={{ margin: 0 }}>
              Tickets
            </div>
            {d.tickets.length === 0 && <span className="muted">Not linked to any ticket you can see.</span>}
            {d.tickets.map((t) => (
              <div key={t.id} className="row-gap">
                <Link className="spacer" to={`/tickets/${t.id}`}>
                  {t.label} {t.subject}
                </Link>
                <StatusChip status={t.status} />
                <span className="muted small">{when(t.created_at)}</span>
              </div>
            ))}
          </section>
        </aside>
      </div>
    </>
  );
}
