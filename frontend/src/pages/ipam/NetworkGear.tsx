import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, type Me, type NetworkDevice } from "../../api/client";
import { DevicePicker } from "../../components/DevicePicker";
import { can } from "../../permissions";

const KIND_LABEL: Record<NetworkDevice["kind"], string> = {
  switch: "Switch",
  printer: "Printer",
  other: "Other",
};

function uptime(seconds: number | null) {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days
    ? `${days} d ${hours} h`
    : `${hours} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** IP addresses > Network gear: what SNMP discovery found, with LLDP neighbours and what's on each switch port. */
export function NetworkGear({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const id = Number(params.get("id")) || null;
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const gear = useQuery({
    queryKey: ["network-devices", debounced],
    queryFn: () => api.network.devices({ q: debounced || undefined }),
  });
  const open = (n: number | null) => setParams(n ? { id: String(n) } : {});

  return (
    <div className="admin">
      <div className="stack" style={{ gap: 8 }}>
        <input
          type="search"
          placeholder="Name, address, model, serial"
          aria-label="Find network gear"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {gear.data?.length === 0 && (
          <p className="muted small">
            {debounced
              ? "Nothing matches."
              : "Nothing discovered yet. Set an SNMP community under Settings > IP addresses, then use Discover (SNMP) on a subnet."}
          </p>
        )}
        <nav className="admin-list report-list" aria-label="Network gear">
          <ul>
            {gear.data?.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  aria-current={n.id === id}
                  onClick={() => open(n.id)}
                >
                  {n.sys_name || n.address}
                  <span className="muted small">
                    {" "}
                    · <span className="mono">{n.address}</span> ·{" "}
                    {[n.vendor, n.model].filter(Boolean).join(" ") ||
                      KIND_LABEL[n.kind]}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </div>
      {id ? (
        <GearDetail
          key={id}
          id={id}
          me={me}
          onGone={() => open(null)}
          onOpen={open}
        />
      ) : (
        <section className="card" style={{ margin: 0 }}>
          <p className="muted" style={{ margin: 0 }}>
            Pick a device to see its LLDP neighbours and what's plugged into
            each port.
          </p>
        </section>
      )}
    </div>
  );
}

function GearDetail({
  id,
  me,
  onGone,
  onOpen,
}: {
  id: number;
  me: Me;
  onGone: () => void;
  onOpen: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["network-device", id],
    queryFn: () => api.network.device(id),
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["network-device", id] });
    queryClient.invalidateQueries({ queryKey: ["network-devices"] });
  };
  const link = useMutation({
    mutationFn: (deviceId: number | null) => api.network.link(id, deviceId),
    onSuccess: refresh,
  });
  const [confirm, setConfirm] = useState(false);
  const forget = useMutation({
    mutationFn: () => api.network.forget(id),
    onSuccess: () => (refresh(), onGone()),
  });
  const n = detail.data;
  if (!n)
    return (
      <p className="muted">
        {detail.error ? detail.error.message : "Loading…"}
      </p>
    );

  return (
    <div className="stack" style={{ gap: 12 }}>
      <section
        className="card stack"
        style={{ margin: 0 }}
        aria-label={`Network device ${n.sys_name || n.address}`}
      >
        <div className="row-gap">
          <h2 className="spacer" style={{ margin: 0 }}>
            {n.sys_name || n.address}{" "}
            <span className="mono muted small">{n.address}</span>
          </h2>
          {can(me, "ipam_delete") &&
            (confirm ? (
              <>
                <span className="small">
                  Forget it? A later discovery that finds it adds it again.
                </span>
                <button
                  className="btn2"
                  type="button"
                  disabled={forget.isPending}
                  onClick={() => forget.mutate()}
                >
                  Forget
                </button>
                <button
                  className="linkish small"
                  type="button"
                  onClick={() => setConfirm(false)}
                >
                  Keep
                </button>
              </>
            ) : (
              <button
                className="btn2"
                type="button"
                onClick={() => setConfirm(true)}
              >
                Forget…
              </button>
            ))}
        </div>
        <dl className="facts">
          <dt>Kind</dt>
          <dd>{KIND_LABEL[n.kind]}</dd>
          {(n.vendor || n.model) && (
            <>
              <dt>Model</dt>
              <dd>{[n.vendor, n.model].filter(Boolean).join(" ")}</dd>
            </>
          )}
          {n.serial && (
            <>
              <dt>Serial</dt>
              <dd className="mono">{n.serial}</dd>
            </>
          )}
          {n.location && (
            <>
              <dt>Location</dt>
              <dd>{n.location}</dd>
            </>
          )}
          {n.contact && (
            <>
              <dt>Contact</dt>
              <dd>{n.contact}</dd>
            </>
          )}
          <dt>Up for</dt>
          <dd>{uptime(n.uptime_seconds)}</dd>
          <dt>Seen</dt>
          <dd>
            {new Date(n.last_seen_at).toLocaleString()}{" "}
            <span className="muted small">
              (first {new Date(n.first_seen_at).toLocaleDateString()})
            </span>
          </dd>
          <dt>Asset</dt>
          <dd>
            {n.device ? (
              <>
                <Link to={`/devices/${n.device.id}`}>{n.device.name}</Link>{" "}
                <button
                  className="linkish small"
                  type="button"
                  disabled={link.isPending}
                  onClick={() => link.mutate(null)}
                >
                  Unlink
                </button>
              </>
            ) : (
              <DevicePicker
                label="Link to a device"
                onPick={(d) => link.mutate(d.id)}
              />
            )}
          </dd>
        </dl>
        {n.sys_descr && (
          <p className="small muted" style={{ margin: 0 }}>
            {n.sys_descr}
          </p>
        )}
        {link.error && <p className="error">{link.error.message}</p>}
        {forget.error && <p className="error">{forget.error.message}</p>}
      </section>

      <section
        className="card stack"
        style={{ margin: 0 }}
        aria-label="Neighbours"
      >
        <h3 style={{ margin: 0 }}>Neighbours (LLDP)</h3>
        {n.neighbors.length === 0 ? (
          <p className="muted small" style={{ margin: 0 }}>
            None seen.
          </p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Our port</th>
                <th>Neighbour</th>
                <th>Their port</th>
                <th>Address</th>
              </tr>
            </thead>
            <tbody>
              {n.neighbors.map((x, i) => (
                <tr key={i}>
                  <td className="mono small">{x.local_port}</td>
                  <td>
                    {x.remote_id ? (
                      <button
                        className="linkish"
                        type="button"
                        onClick={() => onOpen(x.remote_id!)}
                      >
                        {x.remote_name || x.remote_chassis}
                      </button>
                    ) : (
                      x.remote_name || (
                        <span className="mono small">{x.remote_chassis}</span>
                      )
                    )}
                  </td>
                  <td className="small">
                    {x.remote_port_desc || x.remote_port}
                    {x.remote_port_desc &&
                      x.remote_port &&
                      x.remote_port !== x.remote_port_desc && (
                        <span className="muted"> ({x.remote_port})</span>
                      )}
                  </td>
                  <td className="mono small">{x.remote_address ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {n.ports.length > 0 && (
        <section
          className="card stack"
          style={{ margin: 0 }}
          aria-label="Ports"
        >
          <h3 style={{ margin: 0 }}>
            What's on each port ({n.mac_count} MACs)
          </h3>
          <p className="small muted" style={{ margin: 0 }}>
            From the switch's MAC table at the last discovery. Uplinks (to
            other discovered gear, or with many MACs) come last, folded up.
          </p>
          <table className="grid">
            <thead>
              <tr>
                <th>Port</th>
                <th>MAC</th>
                <th>VLAN</th>
                <th>Documented as</th>
              </tr>
            </thead>
            <tbody>
              {n.ports.flatMap((p) =>
                (p.uplink
                  ? p.macs.slice(0, 1)
                  : p.macs
                ).map((m, i) => (
                  <tr key={`${p.port}-${m.mac}`}>
                    <td className="mono small">
                      {i === 0 && (
                        <>
                          {p.port}
                          {p.neighbor && (
                            <span className="muted"> → {p.neighbor}</span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="mono small">
                      {p.uplink ? (
                        <span className="muted">
                          {p.macs.length === 1 ? "1 MAC" : `${p.macs.length} MACs`} (uplink)
                        </span>
                      ) : (
                        m.mac
                      )}
                    </td>
                    <td className="small">
                      {p.uplink ? "" : (m.vlan ?? "")}
                    </td>
                    <td className="small">
                      {p.uplink ? (
                        ""
                      ) : m.address ? (
                        <>
                          <Link
                            className="mono"
                            to={`/ipam/addresses?q=${encodeURIComponent(m.address)}`}
                          >
                            {m.address}
                          </Link>{" "}
                          {m.hostname}
                          {m.device && (
                            <>
                              {" "}
                              ·{" "}
                              <Link to={`/devices/${m.device.id}`}>
                                {m.device.name}
                              </Link>
                            </>
                          )}
                        </>
                      ) : (
                        <span className="muted">not documented</span>
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
