// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api, type NutanixVm } from "../api/client";
import { when } from "../format";

type Action = "restart" | "power_on" | "power_cycle";
const ACTION_LABELS: Record<Action, string> = { restart: "Restart", power_on: "Power on", power_cycle: "Power cycle" };
const ACTION_WARNINGS: Record<Action, string> = {
  restart: "The guest operating system restarts gracefully (through Nutanix Guest Tools when it has them).",
  power_on: "The VM starts.",
  power_cycle: "A hard restart, like pulling the plug: unsaved work in the VM is lost.",
};

export function PowerState({ vm }: { vm: Pick<NutanixVm, "power_state" | "present" | "stale"> }) {
  if (!vm.present) return <span className="chip n">Gone from Nutanix</span>;
  const label = vm.power_state === "on" ? "Running" : vm.power_state === "off" ? "Off" : vm.power_state || "Unknown";
  return (
    <span className={`chip ${vm.power_state === "on" ? "g" : vm.power_state === "off" ? "n" : "a"}`}>
      {label}
      {vm.stale ? " (stale)" : ""}
    </span>
  );
}

/** One VM in a few lines: where it runs, its size and addresses. */
export function VmFacts({ vm }: { vm: NutanixVm }) {
  const memory = vm.memory_mb >= 1024 ? `${Math.round((vm.memory_mb / 1024) * 10) / 10} GB` : `${vm.memory_mb} MB`;
  return (
    <div className="small muted">
      {[vm.host_name, vm.cluster_name].filter(Boolean).join(" · ") || "Host unknown"} · {vm.vcpus} vCPU · {memory}
      {vm.ips.length > 0 && <> · {vm.ips.join(", ")}</>}
      {" · "}
      {vm.guest_tools ? (vm.guest_tools_reachable ? "Guest Tools answering" : "Guest Tools not answering") : "No Guest Tools"}
    </div>
  );
}

/** Devices > Virtual machines: what Prism Central last reported. */
export function NutanixVmList() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [state, setState] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const vms = useQuery({ queryKey: ["nutanix-vms", debounced, state], queryFn: () => api.nutanix.vms({ q: debounced, state }) });
  return (
    <div className="stack">
      <div className="row-gap">
        <input className="spacer" style={{ maxWidth: 420 }} type="search" placeholder="Search name, host, cluster or IP" aria-label="Search VMs" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Power state" style={{ width: "auto" }} value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">All VMs</option>
          <option value="on">Running</option>
          <option value="off">Not running</option>
        </select>
        {vms.data && <span className="muted small">{vms.data.length} VM(s)</span>}
      </div>
      {vms.error && <p className="error">{vms.error.message}</p>}
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Name</th>
              <th>State</th>
              <th>Host and cluster</th>
              <th>Size</th>
              <th>IP addresses</th>
              <th>Device</th>
            </tr>
          </thead>
          <tbody>
            {vms.data?.map((vm) => (
              <tr key={vm.ext_id}>
                <td>{vm.name}</td>
                <td><PowerState vm={vm} /></td>
                <td>{[vm.host_name, vm.cluster_name].filter(Boolean).join(" · ")}</td>
                <td className="small">{vm.vcpus} vCPU · {Math.round(vm.memory_mb / 1024)} GB</td>
                <td className="small">{vm.ips.join(", ")}</td>
                <td>{vm.device_id ? <Link to={`/devices/${vm.device_id}`}>{vm.device_name}</Link> : <span className="muted small">No match</span>}</td>
              </tr>
            ))}
            {vms.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">No VMs match. Settings &gt; Nutanix shows how the last read went.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** On a device: the Nutanix VM matched to it by name. */
export function DeviceVms({ deviceId }: { deviceId: number }) {
  const vms = useQuery({ queryKey: ["nutanix-device-vms", deviceId], queryFn: () => api.nutanix.forDevice(deviceId) });
  if (vms.error) return <p className="error small">{vms.error.message}</p>;
  if (!vms.data?.length) return <span className="muted small">No Nutanix VM matches this device&apos;s name.</span>;
  return (
    <div className="stack" style={{ gap: 6 }}>
      {vms.data.map((vm) => (
        <div key={vm.ext_id}>
          <strong>{vm.name}</strong> <PowerState vm={vm} />
          <VmFacts vm={vm} />
        </div>
      ))}
    </div>
  );
}

/** A ticket's VMs: linked ones and those of its devices, with power actions for those allowed. */
export function TicketVmsCard({ ticketId, onChanged }: { ticketId: number; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const key = ["nutanix-ticket-vms", ticketId];
  const data = useQuery({
    queryKey: key,
    queryFn: () => api.nutanix.forTicket(ticketId),
    refetchInterval: (query) => (query.state.data?.actions.some((a) => a.status === "submitted" || a.status === "running") ? 10000 : false),
  });
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [confirm, setConfirm] = useState<{ extId: string; name: string; action: Action } | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);
  const found = useQuery({ queryKey: ["nutanix-vms", debounced, ""], queryFn: () => api.nutanix.vms({ q: debounced }), enabled: debounced.length >= 2 });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
    onChanged();
  };
  const link = useMutation({ mutationFn: (extId: string) => api.nutanix.link(ticketId, extId), onSuccess: () => { setSearch(""); refresh(); } });
  const unlink = useMutation({ mutationFn: (extId: string) => api.nutanix.unlink(ticketId, extId), onSuccess: refresh });
  const power = useMutation({
    mutationFn: (c: { extId: string; action: Action }) => api.nutanix.power(ticketId, c.extId, c.action),
    onSuccess: () => { setConfirm(null); refresh(); },
  });
  if (data.error) return <p className="error small">{data.error.message}</p>;
  if (!data.data) return null;
  const d = data.data;
  const linked = new Set(d.vms.map((vm) => vm.ext_id));
  const busy = new Set(d.actions.filter((a) => a.status === "submitted" || a.status === "running").map((a) => a.vm_id));
  const actionsFor = (vm: NutanixVm): Action[] => {
    if (!d.power_enabled || !vm.present || busy.has(vm.ext_id)) return [];
    if (vm.power_state !== "on") return d.may_power ? ["power_on"] : [];
    return [...(d.may_power ? (["restart"] as Action[]) : []), ...(d.may_power_cycle ? (["power_cycle"] as Action[]) : [])];
  };
  return (
    <div className="stack" style={{ gap: 10 }}>
      {d.vms.length === 0 && <span className="muted small">No VMs linked. Link one below, or link a device whose name matches a VM.</span>}
      {d.vms.map((vm) => (
        <div key={vm.ext_id} className="stack" style={{ gap: 4 }}>
          <div className="row-gap">
            <strong className="spacer">{vm.name}</strong>
            <PowerState vm={vm} />
          </div>
          <VmFacts vm={vm} />
          <div className="row-gap small">
            {vm.linked ? (
              <button className="linkish" type="button" disabled={unlink.isPending} onClick={() => unlink.mutate(vm.ext_id)}>Unlink</button>
            ) : (
              <span className="muted">Through device {vm.device_id ? <Link to={`/devices/${vm.device_id}`}>{vm.device_name}</Link> : vm.device_name}</span>
            )}
            {busy.has(vm.ext_id) && <span className="muted" role="status">Power action under way…</span>}
            {actionsFor(vm).map((action) => (
              <button key={action} className="btn2" type="button" onClick={() => setConfirm({ extId: vm.ext_id, name: vm.name, action })}>
                {ACTION_LABELS[action]}
              </button>
            ))}
          </div>
          {confirm?.extId === vm.ext_id && (
            <div className="card note stack" role="group" aria-label={`Confirm ${ACTION_LABELS[confirm.action].toLowerCase()}`} style={{ gap: 6, padding: 10 }}>
              <span>
                {ACTION_LABELS[confirm.action]} <strong>{confirm.name}</strong>? {ACTION_WARNINGS[confirm.action]} It&apos;s noted on this ticket.
              </span>
              <div className="row-gap">
                <button className="btn" type="button" disabled={power.isPending} onClick={() => power.mutate(confirm)}>
                  {power.isPending ? "Asking Nutanix…" : `Yes, ${ACTION_LABELS[confirm.action].toLowerCase()}`}
                </button>
                <button className="btn2" type="button" onClick={() => { setConfirm(null); power.reset(); }}>Cancel</button>
              </div>
              {power.error && <p className="error small" role="alert">{power.error.message}</p>}
            </div>
          )}
        </div>
      ))}
      <label className="field small">
        Link a VM
        <input type="search" placeholder="Name, host or IP" value={search} onChange={(e) => setSearch(e.target.value)} />
      </label>
      {found.data && debounced.length >= 2 && (
        <div className="stack" style={{ gap: 4 }}>
          {found.data.filter((vm) => !linked.has(vm.ext_id)).slice(0, 8).map((vm) => (
            <div key={vm.ext_id} className="row-gap small">
              <span className="spacer">{vm.name} <span className="muted">{vm.ips.join(", ")}</span></span>
              <button className="btn2" type="button" disabled={link.isPending} onClick={() => link.mutate(vm.ext_id)}>Link</button>
            </div>
          ))}
          {found.data.length === 0 && <span className="muted small">No VM matches.</span>}
        </div>
      )}
      {(link.error || unlink.error) && <p className="error small" role="alert">{(link.error || unlink.error)?.message}</p>}
      {d.actions.length > 0 && (
        <div className="stack" style={{ gap: 2 }}>
          <div className="lbl" style={{ margin: 0 }}>Power actions</div>
          {d.actions.map((a) => (
            <div key={a.id} className="small">
              {ACTION_LABELS[a.action as Action] ?? a.action} {a.vm_name}: <strong>{a.status}</strong>
              {a.detail && a.status !== "succeeded" ? ` (${a.detail})` : ""}
              <span className="muted"> · {a.requested_by ?? "someone"}, {when(a.requested_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
