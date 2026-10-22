import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { IpamPage } from "./IpamPage";

afterEach(() => vi.unstubAllGlobals());

const VLAN = { id: 2, number: 20, name: "Staff", site: "" };
const USAGE = { hosts: 6, static: 1, reserved: 0, dhcp: 0, do_not_use: 0, pool: 2, free: 2 };
const SUBNET = {
  id: 1,
  network: "10.1.20.0/29",
  name: "Staff",
  vlan: VLAN,
  gateway: "10.1.20.1",
  dhcp_start: "10.1.20.5",
  dhcp_end: "10.1.20.6",
  dns_servers: "",
  site: "",
  notes: "",
  updated_at: "2026-10-20T00:00:00Z",
  usage: USAGE,
};
const PRINTER = {
  id: 7,
  address: "10.1.20.2",
  hostname: "prn-1",
  mac: "aa:bb:cc:00:11:22",
  kind: "static",
  device: null,
  system: null,
  notes: "",
  source: "manual",
  first_seen_at: null,
  last_seen_at: null,
  updated_at: "2026-10-20T00:00:00Z",
  subnet: { id: 1, network: "10.1.20.0/29", name: "Staff" },
};
const cells = ["network", "gateway", "static", "free", "free", "pool", "pool", "broadcast"].map((state, i) => ({
  address: `10.1.20.${i}`,
  state,
  hostname: state === "static" ? "prn-1" : "",
  id: state === "static" ? 7 : null,
}));
const DETAIL = { ...SUBNET, addresses: [PRINTER], cells, children: [] };

test("subnet map: see what's used, click a free address to document it", async () => {
  const calls = mockApi({
    "GET /api/ipam/subnets": [SUBNET],
    "GET /api/ipam/subnets/1": DETAIL,
    "GET /api/systems": [],
    "POST /api/ipam/addresses": ({ body }: Call) => ({ ...PRINTER, ...(body as object), id: 8 }),
  });
  renderAt("/ipam", [{ path: "/ipam", element: <IpamPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: /10.1.20.0\/29 Staff/ }));
  const section = await screen.findByRole("region", { name: "Subnet 10.1.20.0/29" });
  expect(within(section).getByText(/2 free/)).toBeInTheDocument();
  const map = screen.getByRole("grid", { name: "Addresses in 10.1.20.0/29" });
  expect(within(map).getByRole("button", { name: "10.1.20.0 · Network" })).toBeDisabled();
  expect(within(map).getByRole("button", { name: "10.1.20.2 · Static · prn-1" })).toBeInTheDocument();
  await userEvent.click(within(map).getByRole("button", { name: "10.1.20.3 · Free" }));
  const form = screen.getByRole("form", { name: "New address" });
  expect(within(form).getByLabelText("Address")).toHaveValue("10.1.20.3");
  await userEvent.type(within(form).getByLabelText("Host name"), "prn-2");
  await userEvent.selectOptions(within(form).getByLabelText("Type"), "reserved");
  await userEvent.click(within(form).getByRole("button", { name: "Add address" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ address: "10.1.20.3", hostname: "prn-2", kind: "reserved" }));
});

test("find an address by MAC", async () => {
  mockApi({ "GET /api/ipam/addresses": ({ path }: Call) => (path.includes("q=aabb") ? [PRINTER] : []) });
  renderAt("/ipam/addresses", [{ path: "/ipam/:tab", element: <IpamPage me={ME} /> }]);
  await userEvent.type(screen.getByLabelText("Find an address"), "aabb.cc00");
  expect(await screen.findByRole("button", { name: "10.1.20.2" })).toBeInTheDocument();
  expect(screen.getByText("10.1.20.0/29 Staff")).toBeInTheDocument();
});

test("scan now, then apply or dismiss what it found", async () => {
  const FINDING = {
    id: 3, subnet_id: 1, address: "10.1.20.4", kind: "new_host", status: "open", details: { hostname: "mystery", mac: "aa:00:00:00:00:04" }, ip_address_id: null,
    first_found_at: "2026-10-20T00:00:00Z", last_found_at: "2026-10-20T00:00:00Z", resolved_by: null, resolved_at: null,
  };
  let scanned = false;
  const calls = mockApi({
    "GET /api/ipam/subnets": [SUBNET],
    "GET /api/ipam/subnets/1": () =>
      scanned
        ? { ...DETAIL, last_scan: { id: 1, subnet_id: 1, run_id: 9, scheduled: false, created_at: "2026-10-20T00:00:00Z", processed_at: "2026-10-20T00:01:00Z", hosts_seen: 4, new_findings: 1, error: null, status: "succeeded", runner: "lan-hq" } }
        : DETAIL,
    "GET /api/ipam/findings": () => (scanned ? [FINDING] : []),
    "POST /api/ipam/subnets/1/scan": () => ((scanned = true), {}),
    "POST /api/ipam/findings/3/apply": { ...FINDING, status: "applied" },
  });
  renderAt("/ipam", [{ path: "/ipam", element: <IpamPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: /10.1.20.0\/29 Staff/ }));
  await userEvent.click(await screen.findByRole("button", { name: "Scan now" }));
  expect(await screen.findByText(/by lan-hq: 4 answered, 1 new finding/)).toBeInTheDocument();
  const review = await screen.findByRole("region", { name: "To review" });
  expect(within(review).getByText("Answers but isn't documented (mystery, aa:00:00:00:00:04)")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "10.1.20.4 · Free · scan finding" })).toHaveClass("flagged");
  await userEvent.click(within(review).getByRole("button", { name: "Add it" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/ipam/findings/3/apply")).toBe(true));
});

test("read DHCP and DNS when a server is set, and mark a reservation", async () => {
  const FINDING = {
    id: 4, subnet_id: 1, address: "10.1.20.2", kind: "kind_changed", scan_kind: "dhcp_dns", status: "open",
    details: { hostname: "prn-1.corp.example", mac: "aa:bb:cc:00:11:22", kind: "reserved", documented_kind: "static", dns_names: [] }, ip_address_id: 7,
    first_found_at: "2026-10-20T00:00:00Z", last_found_at: "2026-10-20T00:00:00Z", resolved_by: null, resolved_at: null,
  };
  let read = false;
  const calls = mockApi({
    "GET /api/ipam/subnets": [SUBNET],
    "GET /api/ipam/subnets/1": () => ({
      ...DETAIL,
      dhcp_dns: true,
      last_dhcp_dns: read
        ? { id: 2, subnet_id: 1, run_id: 10, kind: "dhcp_dns", scheduled: false, created_at: "2026-10-20T00:00:00Z", processed_at: "2026-10-20T00:01:00Z", hosts_seen: 3, new_findings: 1, error: null, status: "succeeded", runner: "win1" }
        : null,
    }),
    "GET /api/ipam/findings": () => (read ? [FINDING] : []),
    "POST /api/ipam/subnets/1/scan": () => ((read = true), {}),
    "POST /api/ipam/findings/4/apply": { ...FINDING, status: "applied" },
  });
  renderAt("/ipam", [{ path: "/ipam", element: <IpamPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: /10.1.20.0\/29 Staff/ }));
  expect(await screen.findByText("DHCP and DNS not read yet.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Read DHCP and DNS" }));
  expect(calls.find((c) => c.method === "POST")!.path).toBe("/api/ipam/subnets/1/scan?kind=dhcp_dns");
  expect(await screen.findByText(/DHCP and DNS read .* by win1: 3 in DHCP or DNS, 1 new finding/)).toBeInTheDocument();
  const review = await screen.findByRole("region", { name: "To review" });
  expect(within(review).getByText("DHCP has a reservation for it (aa:bb:cc:00:11:22), documented as static")).toBeInTheDocument();
  await userEvent.click(within(review).getByRole("button", { name: "Mark it reserved" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/ipam/findings/4/apply")).toBe(true));
});

test("network gear: neighbours, what's on each port, and switch ports on addresses", async () => {
  const GEAR = {
    id: 5, address: "10.1.20.2", sys_name: "sw-core-01", sys_descr: "Meraki MS225-48FP", sys_object_id: "1.3.6.1.4.1.29671.2.105", location: "HQ IDF-1", contact: "",
    vendor: "Cisco Meraki", model: "MS225-48FP", serial: "Q2XX-ABCD-1234", kind: "switch", uptime_seconds: 90000, device: null, subnet_id: 1,
    first_seen_at: "2026-10-20T00:00:00Z", last_seen_at: "2026-10-21T00:00:00Z", neighbor_count: 1, mac_count: 2,
  };
  const DETAIL_GEAR = {
    ...GEAR,
    neighbors: [{ local_port: "Te1/1/1", remote_name: "sw-access-02", remote_port: "port 1", remote_port_desc: "Port 1", remote_chassis: "00:18:0a:11:22:33", remote_address: "10.1.10.3", remote_id: null }],
    ports: [
      { port: "Gi1/0/1", neighbor: "", uplink: false, macs: [{ mac: "aa:bb:cc:00:11:22", vlan: 20, address: "10.1.20.2", hostname: "prn-1", device: null }] },
      { port: "Te1/1/1", neighbor: "sw-access-02", uplink: true, macs: [{ mac: "aa:bb:cc:00:00:a1", vlan: 20, address: null, hostname: "", device: null }] },
    ],
  };
  mockApi({
    "GET /api/network/devices": [GEAR],
    "GET /api/network/devices/5": DETAIL_GEAR,
  });
  renderAt("/ipam/gear", [{ path: "/ipam/:tab", element: <IpamPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: /sw-core-01/ }));
  const section = await screen.findByRole("region", { name: "Network device sw-core-01" });
  expect(within(section).getByText("Cisco Meraki MS225-48FP")).toBeInTheDocument();
  expect(within(section).getByText("1 d 1 h")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Neighbours" })).getByText("sw-access-02")).toBeInTheDocument();
  const ports = screen.getByRole("region", { name: "Ports" });
  expect(within(ports).getByRole("link", { name: "10.1.20.2" })).toBeInTheDocument();
  expect(within(ports).getByText("1 MAC (uplink)")).toBeInTheDocument();
});

test("an address shows the switch port it's on", async () => {
  mockApi({
    "GET /api/ipam/addresses": [{ ...PRINTER, switch_port: { switch_id: 5, switch: "sw-core-01", port: "Gi1/0/1", vlan: 20, seen_at: "2026-10-21T00:00:00Z", shared: false } }],
  });
  renderAt("/ipam/addresses", [{ path: "/ipam/:tab", element: <IpamPage me={ME} /> }]);
  await userEvent.type(screen.getByLabelText("Find an address"), "prn");
  expect(await screen.findByRole("link", { name: "sw-core-01" })).toHaveAttribute("href", "/ipam/gear?id=5");
  expect(screen.getByText(/Gi1\/0\/1 \(VLAN 20\)/)).toBeInTheDocument();
});
