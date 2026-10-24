// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "../api/client";
import { when } from "../format";
import { MerakiContext } from "./MerakiContext";
import { NetworkContext } from "./NetworkContext";
import { SwitchPortLabel } from "./SwitchPortLabel";

/** A device's IP addresses (IPAM): subnet, VLAN, last seen; addresses whose host name matches it can be linked in one click. */
export function DeviceIps({ deviceId, compact = false }: { deviceId: number; compact?: boolean }) {
  const queryClient = useQueryClient();
  const key = ["device-ips", deviceId];
  const ips = useQuery({ queryKey: key, queryFn: () => api.ipam.forDevice(deviceId) });
  const link = useMutation({ mutationFn: (addressId: number) => api.ipam.linkDevice(addressId, deviceId), onSettled: () => queryClient.invalidateQueries({ queryKey: key }) });
  const rows = ips.data ?? [];
  if (rows.length === 0) return compact ? null : <span className="muted small">No IP address documented. Add one under IP addresses.</span>;
  return (
    <ul className="device-ips small" aria-label="IP addresses">
      {rows.map(({ address: a, linked }) => (
        <li key={a.id}>
          <Link className="mono" to={`/ipam/addresses?q=${encodeURIComponent(a.address)}`}>
            {a.address}
          </Link>
          <span className="muted">
            {" "}
            {[a.subnet && `${a.subnet.network}${a.subnet.name ? ` ${a.subnet.name}` : ""}`, a.subnet?.vlan && `VLAN ${a.subnet.vlan.number}`, !compact && a.mac, a.last_seen_at && `seen ${when(a.last_seen_at)}`]
              .filter(Boolean)
              .join(" · ")}
            {a.switch_port && (
              <>
                {" · on "}
                <SwitchPortLabel port={a.switch_port} />
              </>
            )}
          </span>
          {a.meraki && <MerakiContext observation={a.meraki} />}
          {a.observations?.length ? <NetworkContext observations={a.observations} /> : null}
          {!linked && (
            <>
              {" "}
              <span className="muted">(host name {a.hostname} matches)</span>{" "}
              <button className="linkish small" type="button" disabled={link.isPending} onClick={() => link.mutate(a.id)}>
                Link
              </button>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
