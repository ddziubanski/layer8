import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, type Call } from "../test/util";
import { MerakiAdmin } from "./admin/MerakiAdmin";
import { MerakiClients } from "./ipam/MerakiClients";
import { DeviceIps } from "../components/DeviceIps";

const OBS = {
  network_id: "N_123", network_name: "Example office", client_id: "c1", mac: "00:11:22:33:44:55",
  address: "10.20.1.42", description: "WS-0142", status: "Online", vlan: "20", switch_name: "SW-01",
  switch_serial: "QXXX-EXAM-PLE1", port: "12", connection: "Wired", ssid: "", stale: true,
  last_seen_at: "2026-10-23T07:00:00Z", observed_at: "2026-10-23T07:05:00Z",
};
afterEach(() => vi.unstubAllGlobals());

test("Meraki discovery is requested explicitly and connection results are visible", async () => {
  const calls = mockApi({
    "GET /api/meraki/status": { enabled: true, configured: true, clients: 1, networks: ["N_123"], last_sync_at: null },
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "POST /api/meraki/test": { networks: ["Example office"] },
    "POST /api/system/jobs/meraki/run": {},
  });
  renderAt("/", [{ path: "/", element: <MerakiAdmin me={{...ME, role: "admin"}} /> }]);
  const read = await screen.findByRole("button", { name: "Read Meraki now" });
  await waitFor(() => expect(read).toBeEnabled());
  expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Test selected networks" }));
  expect(await screen.findByText("Connected: Example office")).toBeInTheDocument();
  await userEvent.click(read);
  expect(await screen.findByText(/Discovery requested/)).toBeInTheDocument();
  expect(calls.filter(c => c.method === "POST").map(c => c.path)).toEqual(["/api/meraki/test", "/api/system/jobs/meraki/run"]);
});

test("client search displays independent stale observations", async () => {
  const calls = mockApi({ "GET /api/meraki/clients": ({ path }: Call) => path.includes("q=missing") ? [] : [OBS] });
  renderAt("/", [{ path: "/", element: <MerakiClients /> }]);
  expect(await screen.findByText("WS-0142")).toBeInTheDocument();
  expect(screen.getByText("Stale observation")).toBeInTheDocument();
  expect(screen.getByText(/SW-01 · Port 12 · VLAN 20/)).toBeInTheDocument();
  await userEvent.type(screen.getByRole("searchbox"), "missing");
  expect(await screen.findByText(/No observations match/)).toBeInTheDocument();
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("device context preserves documented IP and calls out Meraki's observed IP", async () => {
  mockApi({ "GET /api/ipam/devices/1/addresses": [{ linked: true, address: {
    id: 1, address: "10.20.1.43", mac: OBS.mac, hostname: "WS-0142", subnet: null, last_seen_at: null, meraki: OBS,
  } }] });
  renderAt("/", [{ path: "/", element: <DeviceIps deviceId={1} compact /> }]);
  expect(await screen.findByRole("link", { name: "10.20.1.43" })).toBeInTheDocument();
  expect(screen.getByText(/Observed IP 10.20.1.42/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Link" })).not.toBeInTheDocument();
});
