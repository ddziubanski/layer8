// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, type Device, type Me, type SystemIn, type SystemItem, type Vendor, type VendorIn } from "../../api/client";
import { DevicePicker } from "../../components/DevicePicker";
import { can } from "../../permissions";

const ROLES = ["support", "sales", "account manager", "escalation", "other"] as const;
type Role = (typeof ROLES)[number];
type AddressKind = NonNullable<SystemIn["addresses"]>[number]["kind"];
const KINDS: [AddressKind, string][] = [
  ["management", "Management"],
  ["vip", "VIP / virtual"],
  ["ilo", "iLO / iDRAC / out-of-band"],
  ["service", "Service"],
  ["other", "Other"],
];
const BLANK_VENDOR: VendorIn = {
  name: "", account_number: "", website: "", support_phone: "", support_email: "", support_portal: "", support_hours: "", contract_level: "", contract_ends: null, notes: "", is_active: true, contacts: [],
};
const BLANK_SYSTEM: SystemIn = { name: "", description: "", vendor_id: null, contact_id: null, notes: "", is_active: true, device_ids: [], addresses: [] };

type Pane = { kind: "vendor"; id: number | "new" } | { kind: "system"; id: number | "new" } | null;

/** Vendors (who to call and how) and the systems they support. */
export function VendorsPage({ me }: { me: Me }) {
  const tab = useParams().tab === "systems" ? "systems" : "vendors";
  const [q, setQ] = useState("");
  const [pane, setPane] = useState<Pane>(null);
  const vendors = useQuery({ queryKey: ["vendors", q], queryFn: () => api.vendors.list({ q }) });
  const systems = useQuery({ queryKey: ["systems", q], queryFn: () => api.systems.list({ q }) });
  const items = tab === "vendors" ? (vendors.data ?? []) : (systems.data ?? []);
  const kind = tab === "vendors" ? "vendor" : "system";
  const open = pane?.kind === kind ? pane : null;

  return (
    <>
      <div className="page-head row-gap">
        <WorkspaceTitle title="Vendors and systems" area="SERVICE DIRECTORY" />
        <button className="btn" type="button" onClick={() => setPane({ kind, id: "new" })}>
          {tab === "vendors" ? "Add a vendor" : "Add a system"}
        </button>
      </div>
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        <Link to="/vendors" className={tab === "vendors" ? "active" : undefined} role="tab" aria-selected={tab === "vendors"}>
          Vendors
        </Link>
        <Link to="/vendors/systems" className={tab === "systems" ? "active" : undefined} role="tab" aria-selected={tab === "systems"}>
          Systems
        </Link>
      </div>
      <div className="admin">
        <nav className="admin-list report-list" aria-label={tab === "vendors" ? "Vendors" : "Systems"}>
          <input type="search" placeholder={tab === "vendors" ? "Search vendors" : "Search name, vendor, IP, device"} aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 8 }} />
          <ul>
            {items.map((it) => (
              <li key={it.id}>
                <button type="button" aria-current={open?.id === it.id} onClick={() => setPane({ kind, id: it.id })}>
                  {it.name}
                  <span className="muted small">
                    {" "}
                    · {"contacts" in it ? `${it.system_count} system(s)` : ((it as SystemItem).vendor?.name ?? "no vendor")}
                  </span>
                </button>
              </li>
            ))}
            {items.length === 0 && <li className="muted small">{q ? "Nothing matches." : tab === "vendors" ? "No vendors yet." : "No systems yet."}</li>}
          </ul>
        </nav>
        {open?.kind === "vendor" && (
          <VendorForm key={String(open.id)} me={me} vendor={open.id === "new" ? null : (vendors.data?.find((v) => v.id === open.id) ?? null)} onDone={(id) => setPane(id ? { kind: "vendor", id } : null)} />
        )}
        {open?.kind === "system" && (
          <SystemForm key={String(open.id)} me={me} system={open.id === "new" ? null : (systems.data?.find((s) => s.id === open.id) ?? null)} onDone={(id) => setPane(id ? { kind: "system", id } : null)} />
        )}
        {!open && (
          <section className="card" style={{ margin: 0 }}>
            <p className="muted" style={{ margin: 0 }}>
              {tab === "vendors"
                ? "Vendors are who you call: account number, support line, portal, hours, contract and contacts."
                : "Systems are what vendors support (phone system, firewall pair, ERP...): which vendor and contact, which devices, and its addresses. Link them on tickets to have the vendor's details at hand."}
            </p>
          </section>
        )}
      </div>
    </>
  );
}

function VendorForm({ me, vendor, onDone }: { me: Me; vendor: Vendor | null; onDone: (id: number | null) => void }) {
  const queryClient = useQueryClient();
  const [v, setV] = useState<VendorIn>(vendor ? { ...vendor, contacts: vendor.contacts.map((c) => ({ ...c, role: c.role as Role })) } : BLANK_VENDOR);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["vendors"] });
  const save = useMutation({ mutationFn: () => (vendor ? api.vendors.update(vendor.id, v) : api.vendors.create(v)), onSuccess: (r) => onDone(r.id), onSettled: refresh });
  const remove = useMutation({ mutationFn: () => api.vendors.remove(vendor!.id), onSuccess: () => onDone(null), onSettled: refresh });
  const field = (key: keyof VendorIn, label: string, type = "text") => (
    <label className="field">
      {label}
      <input type={type} value={(v[key] as string | null) ?? ""} onChange={(e) => setV({ ...v, [key]: e.target.value || (type === "date" ? null : "") })} />
    </label>
  );
  const contacts = v.contacts ?? [];
  const setContact = (i: number, over: object) => setV({ ...v, contacts: contacts.map((c, j) => (j === i ? { ...c, ...over } : c)) });

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={vendor ? vendor.name : "New vendor"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <header className="module-form-head"><p className="workspace-area">SUPPLIER RECORD</p><h2>{vendor ? vendor.name : "New vendor"}</h2></header>
      <label className="field">
        Name
        <input required maxLength={200} value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} />
      </label>
      <div className="form-grid">
        {field("account_number", "Our account / customer number")}
        {field("support_phone", "Support phone", "tel")}
        {field("support_email", "Support email", "email")}
        {field("support_portal", "Support portal (URL)", "url")}
        {field("support_hours", "Support hours, e.g. 24x7 or Mon–Fri 8–6 CT")}
        {field("website", "Website", "url")}
        {field("contract_level", "Contract / support level")}
        {field("contract_ends", "Contract ends", "date")}
      </div>
      {vendor?.support_portal && /^https?:\/\//i.test(vendor.support_portal) && (
        <a href={vendor.support_portal} target="_blank" rel="noreferrer" className="small">
          Open the support portal
        </a>
      )}
      <fieldset className="filter-box">
        <legend>Contacts</legend>
        <div className="stack" style={{ gap: 8 }}>
          {contacts.map((c, i) => (
            <div key={c.id ?? `new-${i}`} className="line-row contact-row" role="group" aria-label={`Contact ${i + 1}`}>
              <input aria-label="Name" placeholder="Name" required value={c.name} onChange={(e) => setContact(i, { name: e.target.value })} />
              <select aria-label="Role" value={c.role ?? "support"} onChange={(e) => setContact(i, { role: e.target.value })}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r[0].toUpperCase() + r.slice(1)}
                  </option>
                ))}
              </select>
              <input aria-label="Email" placeholder="Email" type="email" value={c.email ?? ""} onChange={(e) => setContact(i, { email: e.target.value })} />
              <input aria-label="Phone" placeholder="Phone" type="tel" value={c.phone ?? ""} onChange={(e) => setContact(i, { phone: e.target.value })} />
              <input aria-label="Mobile" placeholder="Mobile" type="tel" value={c.mobile ?? ""} onChange={(e) => setContact(i, { mobile: e.target.value })} />
              <button className="linkish small" type="button" onClick={() => setV({ ...v, contacts: contacts.filter((_, j) => j !== i) })}>
                Remove
              </button>
            </div>
          ))}
          <div>
            <button className="btn2" type="button" onClick={() => setV({ ...v, contacts: [...contacts, { name: "", role: "support", email: "", phone: "", mobile: "", notes: "" }] })}>
              Add a contact
            </button>
          </div>
        </div>
      </fieldset>
      <label className="field">
        Notes <span className="muted small">(how to open a case, escalation path, anything useful)</span>
        <textarea rows={3} value={v.notes ?? ""} onChange={(e) => setV({ ...v, notes: e.target.value })} />
      </label>
      <label className="inline">
        <input type="checkbox" checked={v.is_active ?? true} onChange={(e) => setV({ ...v, is_active: e.target.checked })} /> In use
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {vendor ? "Save" : "Add vendor"}
        </button>
        <button className="btn2" type="button" onClick={() => onDone(null)}>
          Cancel
        </button>
        <span className="spacer" />
        {vendor && can(me, "vendors_delete") && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${vendor.name}? Its systems stay, without a vendor.`) && remove.mutate()}>
            Delete vendor
          </button>
        )}
      </div>
    </form>
  );
}

function SystemForm({ me, system, onDone }: { me: Me; system: SystemItem | null; onDone: (id: number | null) => void }) {
  const queryClient = useQueryClient();
  const vendors = useQuery({ queryKey: ["vendors", ""], queryFn: () => api.vendors.list() });
  const [s, setS] = useState<SystemIn>(
    system
      ? { name: system.name, description: system.description, vendor_id: system.vendor?.id ?? null, contact_id: system.contact?.id ?? null, notes: system.notes, is_active: system.is_active, device_ids: system.devices.map((d) => d.id), addresses: system.addresses.map(({ address, kind, notes }) => ({ address, kind: kind as AddressKind, notes })) }
      : BLANK_SYSTEM,
  );
  const [devices, setDevices] = useState<Pick<Device, "id" | "name">[]>(system?.devices ?? []);
  const vendor = vendors.data?.find((v) => v.id === s.vendor_id);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["systems"] });
  const body = { ...s, device_ids: devices.map((d) => d.id) };
  const save = useMutation({ mutationFn: () => (system ? api.systems.update(system.id, body) : api.systems.create(body)), onSuccess: (r) => onDone(r.id), onSettled: refresh });
  const remove = useMutation({ mutationFn: () => api.systems.remove(system!.id), onSuccess: () => onDone(null), onSettled: refresh });
  const addresses = s.addresses ?? [];
  const setAddress = (i: number, over: object) => setS({ ...s, addresses: addresses.map((a, j) => (j === i ? { ...a, ...over } : a)) });

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={system ? system.name : "New system"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <header className="module-form-head"><p className="workspace-area">SYSTEM RECORD</p><h2>{system ? system.name : "New system"}</h2></header>
      <label className="field">
        Name
        <input required maxLength={200} value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} placeholder="e.g. Phone system, Core switching, ERP" />
      </label>
      <label className="field">
        What it is
        <input value={s.description ?? ""} onChange={(e) => setS({ ...s, description: e.target.value })} />
      </label>
      <div className="form-grid">
        <label className="field">
          Vendor
          <select value={s.vendor_id ?? ""} onChange={(e) => setS({ ...s, vendor_id: e.target.value ? Number(e.target.value) : null, contact_id: null })}>
            <option value="">None</option>
            {vendors.data?.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Contact to call
          <select value={s.contact_id ?? ""} disabled={!vendor} onChange={(e) => setS({ ...s, contact_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{vendor ? "The vendor's support line" : "Choose a vendor first"}</option>
            {vendor?.contacts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.role})
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="filter-box">
        <legend>Addresses</legend>
        <div className="stack" style={{ gap: 8 }}>
          {addresses.map((a, i) => (
            <div key={i} className="line-row address-row" role="group" aria-label={`Address ${i + 1}`}>
              <input aria-label="Address" placeholder="10.0.0.1 or host.corp.example" required value={a.address} onChange={(e) => setAddress(i, { address: e.target.value })} />
              <select aria-label="What it is" value={a.kind ?? "management"} onChange={(e) => setAddress(i, { kind: e.target.value })}>
                {KINDS.map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
              <input aria-label="Note" placeholder="Note" value={a.notes ?? ""} onChange={(e) => setAddress(i, { notes: e.target.value })} />
              <button className="linkish small" type="button" onClick={() => setS({ ...s, addresses: addresses.filter((_, j) => j !== i) })}>
                Remove
              </button>
            </div>
          ))}
          <div>
            <button className="btn2" type="button" onClick={() => setS({ ...s, addresses: [...addresses, { address: "", kind: "management", notes: "" }] })}>
              Add an address
            </button>
          </div>
        </div>
      </fieldset>
      <fieldset className="filter-box">
        <legend>Runs on</legend>
        <div className="stack" style={{ gap: 6 }}>
          {devices.length > 0 && (
            <div className="row-gap">
              {devices.map((d) => (
                <span key={d.id} className="chip n">
                  {d.name}{" "}
                  <button className="linkish small" type="button" aria-label={`Remove ${d.name}`} onClick={() => setDevices(devices.filter((x) => x.id !== d.id))}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <DevicePicker label="Add a device" exclude={devices.map((d) => d.id)} onPick={(d) => setDevices([...devices, d])} />
        </div>
      </fieldset>
      <label className="field">
        Notes
        <textarea rows={3} value={s.notes ?? ""} onChange={(e) => setS({ ...s, notes: e.target.value })} />
      </label>
      <label className="inline">
        <input type="checkbox" checked={s.is_active ?? true} onChange={(e) => setS({ ...s, is_active: e.target.checked })} /> In use
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {system ? "Save" : "Add system"}
        </button>
        <button className="btn2" type="button" onClick={() => onDone(null)}>
          Cancel
        </button>
        <span className="spacer" />
        {system && can(me, "vendors_delete") && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${system.name}?`) && remove.mutate()}>
            Delete system
          </button>
        )}
      </div>
    </form>
  );
}
