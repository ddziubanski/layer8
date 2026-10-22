import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router";

import { api, type IpAddress, type IpAddressIn, type IpFinding, type IpScan, type Me, type Subnet, type SubnetIn, type Vlan, type VlanIn } from "../../api/client";
import { DevicePicker } from "../../components/DevicePicker";
import { SwitchPortLabel } from "../../components/SwitchPortLabel";
import { can } from "../../permissions";
import { NetworkGear } from "./NetworkGear";

type Kind = IpAddressIn["kind"] & string;
const KINDS: [Kind, string][] = [
  ["static", "Static"],
  ["reserved", "DHCP reservation"],
  ["dhcp", "DHCP lease"],
  ["do_not_use", "Reserved, do not use"],
];
const KIND_LABEL = Object.fromEntries(KINDS) as Record<string, string>;
const STATE_LABEL: Record<string, string> = { ...KIND_LABEL, free: "Free", gateway: "Gateway", pool: "DHCP pool", network: "Network", broadcast: "Broadcast" };
const BLANK_SUBNET: SubnetIn = { network: "", name: "", vlan_id: null, gateway: "", dhcp_start: "", dhcp_end: "", dns_servers: "", site: "", notes: "", scan_every_hours: 0, scan_runner_id: null };
const BLANK_VLAN: VlanIn = { number: 1, name: "", site: "", notes: "" };

const blankAddress = (address = ""): IpAddressIn => ({ address, hostname: "", mac: "", kind: "static", device_id: null, system_id: null, notes: "" });

/** Light IPAM: subnets (with a used/free map), VLANs, and documented addresses. */
export function IpamPage({ me }: { me: Me }) {
  const tab = useParams().tab ?? "subnets";
  return (
    <>
      <div className="page-head row-gap">
        <h1 className="spacer">IP addresses</h1>
      </div>
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        {[
          ["subnets", "Subnets", "/ipam"],
          ["addresses", "Find an address", "/ipam/addresses"],
          ["vlans", "VLANs", "/ipam/vlans"],
          ["gear", "Network gear", "/ipam/gear"],
        ].map(([key, label, to]) => (
          <Link key={key} to={to} className={tab === key ? "active" : undefined} role="tab" aria-selected={tab === key}>
            {label}
          </Link>
        ))}
      </div>
      {tab === "addresses" ? <AddressSearch /> : tab === "vlans" ? <Vlans me={me} /> : tab === "gear" ? <NetworkGear me={me} /> : <Subnets me={me} />}
    </>
  );
}

function usageText(s: Subnet) {
  const u = s.usage;
  if (!u) return "";
  return u.free === null ? `${u.static + u.reserved + u.dhcp + u.do_not_use} documented` : `${u.free} free`;
}

function Subnets({ me }: { me: Me }) {
  const [q, setQ] = useState("");
  const [pane, setPane] = useState<{ id: number; edit?: boolean } | "new" | null>(null);
  const subnets = useQuery({
    queryKey: ["subnets", q],
    queryFn: () => api.ipam.subnets({ q }),
  });
  const items = subnets.data ?? [];
  return (
    <div className="admin">
      <div className="stack" style={{ gap: 8 }}>
        <div className="row-gap">
          <input type="search" className="spacer" placeholder="Network, name, VLAN or an IP" aria-label="Search subnets" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn" type="button" onClick={() => setPane("new")}>
            Add
          </button>
        </div>
        <nav className="admin-list report-list" aria-label="Subnets">
          <ul>
            {items.map((s) => (
              <li key={s.id}>
                <button type="button" aria-current={pane !== "new" && pane?.id === s.id} onClick={() => setPane({ id: s.id })}>
                  <span className="mono">{s.network}</span> {s.name}
                  <span className="muted small">
                    {" "}
                    · {s.vlan ? `VLAN ${s.vlan.number} · ` : ""}
                    {usageText(s)}
                    {s.open_findings > 0 && <> · {s.open_findings} to review</>}
                  </span>
                </button>
              </li>
            ))}
            {items.length === 0 && <li className="muted small">{q ? "Nothing matches." : "No subnets yet."}</li>}
          </ul>
        </nav>
      </div>
      {pane === "new" && <SubnetForm subnet={null} me={me} onDone={(id) => setPane(id ? { id } : null)} />}
      {pane !== "new" && pane?.edit && <SubnetForm key={pane.id} subnet={items.find((s) => s.id === pane.id) ?? null} me={me} onDone={(id) => setPane(id ? { id } : null)} />}
      {pane !== "new" && pane && !pane.edit && <SubnetView key={pane.id} id={pane.id} onEdit={() => setPane({ id: pane.id, edit: true })} onOpen={(id) => setPane({ id })} />}
      {!pane && (
        <section className="card" style={{ margin: 0 }}>
          <p className="muted" style={{ margin: 0 }}>
            Pick a subnet to see what's used, reserved and free, or search for an IP address to find the subnet it's in.
          </p>
        </section>
      )}
    </div>
  );
}

const SCAN_EVERY: [number, string][] = [
  [0, "Only when asked"],
  [1, "Every hour"],
  [4, "Every 4 hours"],
  [12, "Every 12 hours"],
  [24, "Daily"],
  [168, "Weekly"],
];

const SCAN_WORDS: Record<IpScan["kind"], { never: string; what: string; doing: string; found: string; every: string }> = {
  network: { never: "Not scanned yet", what: "scan", doing: "Scanning", found: "answered", every: "scanned" },
  dhcp_dns: { never: "DHCP and DNS not read yet", what: "DHCP and DNS read", doing: "Reading DHCP and DNS", found: "in DHCP or DNS", every: "read" },
  snmp: { never: "No SNMP discovery yet", what: "SNMP discovery", doing: "Discovering over SNMP", found: "answered SNMP (see Network gear)", every: "discovered" },
};

const scanBusy = (scan: IpScan | null | undefined) => !!scan && ["awaiting_approval", "queued", "running"].includes(scan.status);

function ScanLine({ scan, every, kind = "network" }: { scan: IpScan | null; every: number; kind?: IpScan["kind"] }) {
  const schedule = SCAN_EVERY.find(([h]) => h === every)?.[1].toLowerCase() ?? `every ${every} hours`;
  const words = SCAN_WORDS[kind];
  if (!scan)
    return (
      <p className="small muted" style={{ margin: 0 }}>
        {words.never}
        {every ? ` (scheduled ${schedule})` : ""}.
      </p>
    );
  const when = new Date(scan.created_at).toLocaleString();
  let text: string;
  if (scan.status === "queued" || scan.status === "awaiting_approval") text = `${words.what[0].toUpperCase()}${words.what.slice(1)} waiting for a runner (asked ${when})…`;
  else if (scan.status === "running") text = `${words.doing} on ${scan.runner ?? "a runner"}…`;
  else if (scan.error) text = `Last ${words.what} ${when} failed: ${scan.error}`;
  else
    text = `Last ${words.what} ${when}${scan.runner ? ` by ${scan.runner}` : ""}: ${scan.hosts_seen} ${words.found}${kind === "snmp" ? "" : `, ${scan.new_findings} new finding(s)`}`;
  return (
    <p className={`small${scan.error ? " error" : ""}`} style={{ margin: 0 }} role="status">
      {text}
      {every > 0 && (
        <span className="muted">
          {" "}
          · {words.every} {schedule}
        </span>
      )}
    </p>
  );
}

const DHCP_KIND: Record<string, string> = { reserved: "a DHCP reservation", dhcp: "a DHCP lease", static: "static", do_not_use: "do not use" };

function describe(f: IpFinding): { text: string; apply: string | null } {
  const d = f.details as Record<string, string>;
  const fromDhcp = f.scan_kind === "dhcp_dns";
  const seenAs = [fromDhcp && d.kind ? DHCP_KIND[d.kind] : "", d.hostname, d.mac].filter(Boolean).join(", ");
  switch (f.kind) {
    case "new_host":
      return { text: `${fromDhcp ? "In DHCP or DNS" : "Answers"} but isn't documented${seenAs ? ` (${seenAs})` : ""}`, apply: "Add it" };
    case "kind_changed":
      return {
        text: `${d.kind === "reserved" ? "DHCP has a reservation for it" : "DHCP leases it out"}${d.mac ? ` (${d.mac})` : ""}, documented as ${DHCP_KIND[d.documented_kind] ?? d.documented_kind}`, apply: d.kind === "reserved" ? "Mark it reserved" : "Mark it DHCP",
      };
    case "not_seen":
      return { text: `${d.documented_hostname || "Documented"}, not seen since ${new Date(d.last_seen_at).toLocaleDateString()}`, apply: "Free it" };
    case "hostname_changed":
      return { text: `${fromDhcp ? "DHCP or DNS calls it" : "Answers as"} ${d.hostname}, documented as ${d.documented_hostname}`, apply: "Use the new name" };
    case "mac_changed":
      return { text: `MAC is now ${d.mac}, documented ${d.documented_mac}${d.hostname ? ` (${d.hostname})` : ""}`, apply: "Use the new MAC" };
    default:
      return { text: `Marked "do not use", but ${fromDhcp ? "DHCP or DNS has it" : "something answers"}${seenAs ? ` (${seenAs})` : ""}`, apply: null };
  }
}

function Findings({ findings, subnetId }: { findings: IpFinding[]; subnetId: number }) {
  const queryClient = useQueryClient();
  const act = useMutation({
    mutationFn: ({ f, apply }: { f: IpFinding; apply: boolean }) => (apply ? api.ipam.applyFinding(f.id) : api.ipam.dismissFinding(f.id)),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["findings", subnetId] });
      queryClient.invalidateQueries({ queryKey: ["subnet", subnetId] });
      queryClient.invalidateQueries({ queryKey: ["subnets"] });
    },
  });
  return (
    <section className="card stack" style={{ margin: 0 }} aria-label="To review">
      <h3 style={{ margin: 0 }}>To review ({findings.length})</h3>
      <p className="small muted" style={{ margin: 0 }}>
        What scans found that differs from what's documented. Nothing changes until you apply it; dismissed differences aren't raised again.
      </p>
      <table className="grid">
        <tbody>
          {findings.map((f) => {
            const { text, apply } = describe(f);
            return (
              <tr key={f.id}>
                <td className="mono">{f.address}</td>
                <td>{text}</td>
                <td className="small muted">{new Date(f.last_found_at).toLocaleString()}</td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  {apply && (
                    <button className="btn2" type="button" disabled={act.isPending} onClick={() => act.mutate({ f, apply: true })}>
                      {apply}
                    </button>
                  )}{" "}
                  <button className="linkish small" type="button" disabled={act.isPending} onClick={() => act.mutate({ f, apply: false })}>
                    Dismiss
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {act.error && <p className="error">{act.error.message}</p>}
    </section>
  );
}

function SubnetView({ id, onEdit, onOpen }: { id: number; onEdit: () => void; onOpen: (id: number) => void }) {
  const queryClient = useQueryClient();
  const subnet = useQuery({
    queryKey: ["subnet", id],
    queryFn: () => api.ipam.subnet(id),
    // Watch a scan that's waiting or running.
    refetchInterval: (q) => (scanBusy(q.state.data?.last_scan) || scanBusy(q.state.data?.last_dhcp_dns) || scanBusy(q.state.data?.last_snmp) ? 3000 : false),
  });
  const findings = useQuery({
    queryKey: ["findings", id, subnet.data?.last_scan?.processed_at, subnet.data?.last_dhcp_dns?.processed_at],
    queryFn: () => api.ipam.findings({ subnet_id: id }),
  });
  const scan = useMutation({ mutationFn: (kind: IpScan["kind"]) => api.ipam.scan(id, kind), onSettled: () => queryClient.invalidateQueries({ queryKey: ["subnet", id] }) });
  const [editing, setEditing] = useState<IpAddress | IpAddressIn | null>(null);
  const nextFree = useMutation({
    mutationFn: () => api.ipam.nextFree(id),
    onSuccess: (r) => setEditing(blankAddress(r.address ?? "")),
  });
  const s = subnet.data;
  if (!s) return <p className="muted">{subnet.error ? subnet.error.message : "Loading…"}</p>;
  const u = s.usage!;
  const byId = new Map(s.addresses.map((a) => [a.id, a]));
  const flagged = new Set(findings.data?.map((f) => f.address));

  return (
    <div className="stack" style={{ gap: 12 }}>
      <section className="card stack" style={{ margin: 0 }} aria-label={`Subnet ${s.network}`}>
        <div className="row-gap">
          <h2 className="spacer" style={{ margin: 0 }}>
            <span className="mono">{s.network}</span> {s.name}
          </h2>
          <button className="btn2" type="button" onClick={onEdit}>
            Edit subnet
          </button>
          <button className="btn2" type="button" disabled={scan.isPending || scanBusy(s.last_scan)} onClick={() => scan.mutate("network")}>
            Scan now
          </button>
          {s.dhcp_dns && (
            <button className="btn2" type="button" disabled={scan.isPending || scanBusy(s.last_dhcp_dns)} onClick={() => scan.mutate("dhcp_dns")}>
              Read DHCP and DNS
            </button>
          )}
          {s.snmp && (
            <button className="btn2" type="button" disabled={scan.isPending || scanBusy(s.last_snmp)} onClick={() => scan.mutate("snmp")}>
              Discover (SNMP)
            </button>
          )}
          <button className="btn" type="button" onClick={() => nextFree.mutate()}>
            Use the next free address
          </button>
        </div>
        <dl className="facts">
          {s.vlan && (
            <>
              <dt>VLAN</dt>
              <dd>
                {s.vlan.number} {s.vlan.name}
              </dd>
            </>
          )}
          {s.gateway && (
            <>
              <dt>Gateway</dt>
              <dd className="mono">{s.gateway}</dd>
            </>
          )}
          {s.dhcp_start && (
            <>
              <dt>DHCP pool</dt>
              <dd className="mono">
                {s.dhcp_start} – {s.dhcp_end}
              </dd>
            </>
          )}
          {s.dns_servers && (
            <>
              <dt>DNS</dt>
              <dd className="mono">{s.dns_servers}</dd>
            </>
          )}
          {s.site && (
            <>
              <dt>Site</dt>
              <dd>{s.site}</dd>
            </>
          )}
        </dl>
        <p className="small" style={{ margin: 0 }}>
          {u.hosts} usable · <b>{u.free ?? "—"} free</b> · {u.static} static · {u.reserved} DHCP reservations · {u.dhcp} leases · {u.do_not_use} do-not-use · {u.pool} in the DHCP pool
        </p>
        <ScanLine scan={s.last_scan ?? null} every={s.scan_every_hours} />
        {s.dhcp_dns && <ScanLine scan={s.last_dhcp_dns ?? null} every={s.scan_every_hours} kind="dhcp_dns" />}
        {s.snmp && <ScanLine scan={s.last_snmp ?? null} every={s.scan_every_hours} kind="snmp" />}
        {scan.error && <p className="error">{scan.error.message}</p>}
        {nextFree.data && nextFree.data.address === null && <p className="error">No free address outside the DHCP pool.</p>}
        {s.notes && (
          <p className="small body-text" style={{ margin: 0 }}>
            {s.notes}
          </p>
        )}
        {s.children.length > 0 && (
          <p className="small" style={{ margin: 0 }}>
            Smaller subnets inside:{" "}
            {s.children.map((c, i) => (
              <span key={c.id}>
                {i > 0 && ", "}
                <button className="linkish" type="button" onClick={() => onOpen(c.id)}>
                  {c.network} {c.name}
                </button>
              </span>
            ))}
          </p>
        )}
      </section>

      {editing && <AddressForm key={"id" in editing ? editing.id : editing.address} address={editing} onDone={() => setEditing(null)} />}

      {(findings.data?.length ?? 0) > 0 && <Findings findings={findings.data!} subnetId={id} />}

      {s.cells && (
        <section className="card" style={{ margin: 0 }} aria-label="Address map">
          <ul className="ip-legend small" aria-label="Legend">
            {["free", "static", "reserved", "dhcp", "do_not_use", "gateway", "pool"].map((k) => (
              <li key={k}>
                <span className={`ip-cell ${k}`} aria-hidden="true" /> {STATE_LABEL[k]}
              </li>
            ))}
          </ul>
          <div className="ip-map" role="grid" aria-label={`Addresses in ${s.network}`}>
            {s.cells.map((c) => {
              const last = c.address.split(/[.:]/).pop();
              const fixed = c.state === "network" || c.state === "broadcast";
              const title = `${c.address} · ${STATE_LABEL[c.state]}${c.hostname ? ` · ${c.hostname}` : ""}${flagged.has(c.address) ? " · scan finding" : ""}`;
              return (
                <button
                  key={c.address}
                  type="button"
                  className={`ip-cell ${c.state}${flagged.has(c.address) ? " flagged" : ""}`}
                  title={title}
                  aria-label={title}
                  disabled={fixed}
                  onClick={() => setEditing(c.id ? byId.get(c.id)! : blankAddress(c.address))}
                >
                  {last}
                </button>
              );
            })}
          </div>
        </section>
      )}

      <section className="card" style={{ margin: 0 }} aria-label="Documented addresses">
        <AddressTable rows={s.addresses} onPick={setEditing} empty="Nothing documented in this subnet yet." />
      </section>
    </div>
  );
}

function SubnetForm({ subnet, me, onDone }: { subnet: Subnet | null; me: Me; onDone: (id: number | null) => void }) {
  const queryClient = useQueryClient();
  const vlans = useQuery({ queryKey: ["vlans"], queryFn: api.ipam.vlans });
  const runners = useQuery({ queryKey: ["scan-runners"], queryFn: api.ipam.runners });
  const [s, setS] = useState<SubnetIn>(
    subnet
      ? {
          network: subnet.network,
          name: subnet.name,
          vlan_id: subnet.vlan?.id ?? null,
          gateway: subnet.gateway ?? "",
          dhcp_start: subnet.dhcp_start ?? "",
          dhcp_end: subnet.dhcp_end ?? "",
          dns_servers: subnet.dns_servers,
          site: subnet.site,
          notes: subnet.notes,
          scan_every_hours: subnet.scan_every_hours,
          scan_runner_id: subnet.scan_runner_id,
        }
      : BLANK_SUBNET,
  );
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["subnets"] });
    queryClient.invalidateQueries({ queryKey: ["subnet"] });
  };
  const body = {
    ...s,
    gateway: s.gateway || null,
    dhcp_start: s.dhcp_start || null,
    dhcp_end: s.dhcp_end || null,
  };
  const save = useMutation({
    mutationFn: () => (subnet ? api.ipam.updateSubnet(subnet.id, body) : api.ipam.createSubnet(body)),
    onSuccess: (r) => onDone(r.id),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.ipam.removeSubnet(subnet!.id),
    onSuccess: () => onDone(null),
    onSettled: refresh,
  });
  const field = (key: keyof SubnetIn, label: string, placeholder = "") => (
    <label className="field">
      {label}
      <input placeholder={placeholder} value={(s[key] as string | null) ?? ""} onChange={(e) => setS({ ...s, [key]: e.target.value })} />
    </label>
  );

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={subnet ? `Edit ${subnet.network}` : "New subnet"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          Network
          <input required placeholder="10.1.20.0/24" value={s.network} onChange={(e) => setS({ ...s, network: e.target.value })} />
        </label>
        {field("name", "Name", "e.g. Staff, Printers, Servers")}
        <label className="field">
          VLAN
          <select
            value={s.vlan_id ?? ""}
            onChange={(e) =>
              setS({
                ...s,
                vlan_id: e.target.value ? Number(e.target.value) : null,
              })
            }
          >
            <option value="">None</option>
            {vlans.data?.map((v) => (
              <option key={v.id} value={v.id}>
                {v.number} {v.name}
                {v.site ? ` (${v.site})` : ""}
              </option>
            ))}
          </select>
        </label>
        {field("gateway", "Gateway", "10.1.20.1")}
        {field("dhcp_start", "DHCP pool starts", "10.1.20.100")}
        {field("dhcp_end", "DHCP pool ends", "10.1.20.199")}
        {field("dns_servers", "DNS servers", "10.1.0.10, 10.1.0.11")}
        {field("site", "Site")}
        <label className="field">
          Scan it
          <select value={s.scan_every_hours ?? 0} onChange={(e) => setS({ ...s, scan_every_hours: Number(e.target.value) })}>
            {SCAN_EVERY.map(([h, label]) => (
              <option key={h} value={h}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Scanned by
          <select value={s.scan_runner_id ?? ""} onChange={(e) => setS({ ...s, scan_runner_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Any runner that takes Python</option>
            {runners.data?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Scans ping every address, look up reverse DNS and read the runner's ARP table. Pick a runner on this subnet's LAN: it then also finds devices that ignore ping, with their MAC addresses.
        When a Windows DHCP or DNS server is set under Settings &gt; IP addresses, scheduled scans also read its leases, reservations and A records for the subnet; when an SNMP community is set, they also discover the subnet's network gear (on the same runner).
      </p>
      <label className="field">
        Notes
        <textarea rows={3} value={s.notes ?? ""} onChange={(e) => setS({ ...s, notes: e.target.value })} />
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {subnet ? "Save" : "Add subnet"}
        </button>
        <button className="btn2" type="button" onClick={() => onDone(subnet?.id ?? null)}>
          Cancel
        </button>
        <span className="spacer" />
        {subnet && can(me, "ipam_delete") && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete ${subnet.network}? The addresses documented in it stay.`) && remove.mutate()}>
            Delete subnet
          </button>
        )}
      </div>
    </form>
  );
}

function AddressTable({ rows, onPick, empty }: { rows: IpAddress[]; onPick: (a: IpAddress) => void; empty: string }) {
  if (rows.length === 0)
    return (
      <p className="muted" style={{ margin: 0 }}>
        {empty}
      </p>
    );
  return (
    <table className="grid">
      <thead>
        <tr>
          <th>Address</th>
          <th>Host name</th>
          <th>MAC</th>
          <th>Type</th>
          <th>Device / system</th>
          <th>Subnet</th>
          <th>Last seen</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.id}>
            <td className="mono">
              <button className="linkish" type="button" onClick={() => onPick(a)}>
                {a.address}
              </button>
            </td>
            <td>{a.hostname}</td>
            <td className="mono small">
              {a.mac}
              {a.switch_port && (
                <div className="muted">
                  <SwitchPortLabel port={a.switch_port} />
                </div>
              )}
            </td>
            <td>{KIND_LABEL[a.kind]}</td>
            <td>{[a.device?.name, a.system?.name].filter(Boolean).join(" · ")}</td>
            <td className="small">{a.subnet ? `${a.subnet.network} ${a.subnet.name}` : <span className="muted">not in a subnet</span>}</td>
            <td className="small muted">{a.last_seen_at ? new Date(a.last_seen_at).toLocaleDateString() : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AddressForm({ address, onDone }: { address: IpAddress | IpAddressIn; onDone: () => void }) {
  const queryClient = useQueryClient();
  const existing = "id" in address ? address : null;
  const systems = useQuery({
    queryKey: ["systems", ""],
    queryFn: () => api.systems.list(),
  });
  const [a, setA] = useState<IpAddressIn>(
    existing
      ? {
          address: existing.address,
          hostname: existing.hostname,
          mac: existing.mac,
          kind: existing.kind,
          device_id: existing.device?.id ?? null,
          system_id: existing.system?.id ?? null,
          notes: existing.notes,
        }
      : (address as IpAddressIn),
  );
  const [device, setDevice] = useState<{ id: number; name: string } | null>(existing?.device ?? null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["subnet"] });
    queryClient.invalidateQueries({ queryKey: ["subnets"] });
    queryClient.invalidateQueries({ queryKey: ["ip-search"] });
  };
  const body = { ...a, device_id: device?.id ?? null };
  const save = useMutation({
    mutationFn: () => (existing ? api.ipam.updateAddress(existing.id, body) : api.ipam.createAddress(body)),
    onSuccess: onDone,
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.ipam.removeAddress(existing!.id),
    onSuccess: onDone,
    onSettled: refresh,
  });

  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={existing ? `Address ${existing.address}` : "New address"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          Address
          <input required value={a.address} onChange={(e) => setA({ ...a, address: e.target.value })} />
        </label>
        <label className="field">
          Host name
          <input value={a.hostname ?? ""} onChange={(e) => setA({ ...a, hostname: e.target.value })} />
        </label>
        <label className="field">
          MAC address
          <input placeholder="any format" value={a.mac ?? ""} onChange={(e) => setA({ ...a, mac: e.target.value })} />
        </label>
        <label className="field">
          Type
          <select value={a.kind ?? "static"} onChange={(e) => setA({ ...a, kind: e.target.value as Kind })}>
            {KINDS.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          System
          <select
            value={a.system_id ?? ""}
            onChange={(e) =>
              setA({
                ...a,
                system_id: e.target.value ? Number(e.target.value) : null,
              })
            }
          >
            <option value="">None</option>
            {systems.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {device ? (
          <div className="field">
            Device
            <span>
              <span className="chip n">{device.name}</span>{" "}
              <button className="linkish small" type="button" onClick={() => setDevice(null)}>
                Remove
              </button>
            </span>
          </div>
        ) : (
          <DevicePicker label="Device" onPick={(d) => setDevice(d)} />
        )}
      </div>
      <label className="field">
        Notes
        <textarea rows={2} value={a.notes ?? ""} onChange={(e) => setA({ ...a, notes: e.target.value })} />
      </label>
      {existing?.last_seen_at && (
        <p className="small muted" style={{ margin: 0 }}>
          Seen by scans {new Date(existing.first_seen_at!).toLocaleString()} – {new Date(existing.last_seen_at).toLocaleString()}
          {existing.switch_port && (
            <>
              {" "}
              · on <SwitchPortLabel port={existing.switch_port} />
            </>
          )}
        </p>
      )}
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {existing ? "Save" : "Add address"}
        </button>
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
        <span className="spacer" />
        {existing && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Free ${existing.address}? Its record is removed.`) && remove.mutate()}>
            Free this address
          </button>
        )}
      </div>
    </form>
  );
}

function AddressSearch() {
  // ?q= from links elsewhere (a device's addresses).
  const search = useLocation().search;
  const [q, setQ] = useState(() => new URLSearchParams(search).get("q") ?? "");
  const [editing, setEditing] = useState<IpAddress | IpAddressIn | null>(null);
  const results = useQuery({
    queryKey: ["ip-search", q],
    queryFn: () => api.ipam.addresses({ q }),
    enabled: q.trim().length >= 2,
  });
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row-gap">
        <input
          type="search"
          className="spacer"
          placeholder="IP (or its start: 10.1.20.), host name, MAC in any format, device or system"
          aria-label="Find an address"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="btn" type="button" onClick={() => setEditing(blankAddress())}>
          Add an address
        </button>
      </div>
      {editing && <AddressForm key={"id" in editing ? editing.id : "new"} address={editing} onDone={() => setEditing(null)} />}
      {q.trim().length >= 2 && (
        <section className="card" style={{ margin: 0 }}>
          <AddressTable rows={results.data ?? []} onPick={setEditing} empty={results.isLoading ? "Searching…" : "Nothing documented matches."} />
        </section>
      )}
    </div>
  );
}

function Vlans({ me }: { me: Me }) {
  const vlans = useQuery({ queryKey: ["vlans"], queryFn: api.ipam.vlans });
  const [editing, setEditing] = useState<Vlan | "new" | null>(null);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row-gap">
        <span className="spacer" />
        <button className="btn" type="button" onClick={() => setEditing("new")}>
          Add a VLAN
        </button>
      </div>
      {editing && <VlanForm key={editing === "new" ? "new" : editing.id} vlan={editing === "new" ? null : editing} me={me} onDone={() => setEditing(null)} />}
      <section className="card" style={{ margin: 0 }}>
        {vlans.data?.length ? (
          <table className="grid">
            <thead>
              <tr>
                <th>VLAN</th>
                <th>Name</th>
                <th>Site</th>
                <th>Subnets</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {vlans.data.map((v) => (
                <tr key={v.id}>
                  <td>
                    <button className="linkish" type="button" onClick={() => setEditing(v)}>
                      {v.number}
                    </button>
                  </td>
                  <td>{v.name}</td>
                  <td>{v.site}</td>
                  <td>{v.subnet_count}</td>
                  <td className="small">{v.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            No VLANs yet.
          </p>
        )}
      </section>
    </div>
  );
}

function VlanForm({ vlan, me, onDone }: { vlan: Vlan | null; me: Me; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [v, setV] = useState<VlanIn>(
    vlan
      ? {
          number: vlan.number,
          name: vlan.name,
          site: vlan.site,
          notes: vlan.notes,
        }
      : BLANK_VLAN,
  );
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["vlans"] });
  const save = useMutation({
    mutationFn: () => (vlan ? api.ipam.updateVlan(vlan.id, v) : api.ipam.createVlan(v)),
    onSuccess: onDone,
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.ipam.removeVlan(vlan!.id),
    onSuccess: onDone,
    onSettled: refresh,
  });
  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={vlan ? `VLAN ${vlan.number}` : "New VLAN"}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          VLAN number
          <input required type="number" min={1} max={4094} value={v.number} onChange={(e) => setV({ ...v, number: Number(e.target.value) })} />
        </label>
        <label className="field">
          Name
          <input value={v.name ?? ""} onChange={(e) => setV({ ...v, name: e.target.value })} />
        </label>
        <label className="field">
          Site
          <input value={v.site ?? ""} onChange={(e) => setV({ ...v, site: e.target.value })} />
        </label>
      </div>
      <label className="field">
        Notes
        <textarea rows={2} value={v.notes ?? ""} onChange={(e) => setV({ ...v, notes: e.target.value })} />
      </label>
      {(save.error ?? remove.error) && <p className="error">{(save.error ?? remove.error)!.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={save.isPending}>
          {vlan ? "Save" : "Add VLAN"}
        </button>
        <button className="btn2" type="button" onClick={onDone}>
          Cancel
        </button>
        <span className="spacer" />
        {vlan && can(me, "ipam_delete") && (
          <button className="linkish small" type="button" onClick={() => window.confirm(`Delete VLAN ${vlan.number}? Its subnets stay, without a VLAN.`) && remove.mutate()}>
            Delete VLAN
          </button>
        )}
      </div>
    </form>
  );
}
