import { Link } from "react-router";

import type { SwitchPort } from "../api/client";

/** Where a MAC plugs in (SNMP discovery): switch, port and VLAN, linked to the switch's page. */
export function SwitchPortLabel({ port }: { port: SwitchPort }) {
  return (
    <span
      title={`From ${port.switch}'s MAC table, ${new Date(port.seen_at).toLocaleString()}${port.shared ? ". Other MACs were on that port too (a phone, a small switch or an access point, or an uplink)." : ""}`}
    >
      <Link to={`/ipam/gear?id=${port.switch_id}`}>{port.switch}</Link>{" "}
      {port.port}
      {port.vlan != null && ` (VLAN ${port.vlan})`}
      {port.shared && " · shared"}
    </span>
  );
}
