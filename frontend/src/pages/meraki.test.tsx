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

test("webhook setup and safe recent delivery links are visible to administrators", async () => {
  const calls = mockApi({
    "GET /api/meraki/status": { enabled: false, configured: false, clients: 0, networks: [] },
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "GET /api/meraki/webhooks/status": { enabled: true, configured: true,
      endpoint: "https://helpdesk.corp.example/api/meraki/webhooks", deliveries: [{ network_id: "N_123", serial: "QXXX-EXAM-PLE1",
        alert_type: "started_reporting", occurred_at: "2026-10-23T07:00:00Z", received_at: "2026-10-23T07:02:00Z", outcome: "recovery", ticket_id: 4821 }] },
  });
  renderAt("/", [{ path: "/", element: <MerakiAdmin me={{ ...ME, role: "admin" }} /> }]);
  expect(await screen.findByText("Intake enabled")).toBeInTheDocument();
  expect(screen.getByText("https://helpdesk.corp.example/api/meraki/webhooks")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Ticket #4821" })).toHaveAttribute("href", "/tickets/4821");
  expect(screen.getByText("Recovery noted")).toBeInTheDocument();
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("agents cannot read the administrator webhook setup", () => {
  const calls = mockApi({});
  renderAt("/", [{ path: "/", element: <MerakiAdmin me={ME} /> }]);
  expect(screen.getByText("Meraki is managed by admins.")).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});

test("a failed webhook status read does not claim intake is off", async () => {
  mockApi({
    "GET /api/meraki/status": { enabled: false, configured: false, clients: 0, networks: [] },
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "GET /api/meraki/webhooks/status": new Response(JSON.stringify({ detail: "synthetic private provider error" }), { status: 503 }),
  });
  renderAt("/", [{ path: "/", element: <MerakiAdmin me={{ ...ME, role: "admin" }} /> }]);
  expect(await screen.findByText("Status unavailable")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Could not read webhook delivery history.");
  expect(screen.queryByText("Intake off")).not.toBeInTheDocument();
  expect(screen.queryByText("synthetic private provider error")).not.toBeInTheDocument();
});

test("Meraki discovery is requested explicitly and connection results are visible", async () => {
  const calls = mockApi({
    "GET /api/meraki/status": { enabled: true, configured: true, clients: 1, networks: ["N_123"], last_sync_at: null },
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "GET /api/meraki/webhooks/status": { enabled: false, configured: false, endpoint: null, deliveries: [] },
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

const OAUTH = {
  enabled: true, configured: true, clients: 2, networks: ["N_123"], auth_mode: "oauth",
  oauth: { configured: true, connected: true, redirect_uri: "https://helpdesk.corp.example/api/meraki/oauth/callback" },
};

test("OAuth disconnect is explicit and warns when remote revocation fails", async () => {
  let connected = true;
  const calls = mockApi({
    "GET /api/meraki/status": () => ({ ...OAUTH, configured: connected, oauth: { ...OAUTH.oauth, connected } }),
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "GET /api/meraki/webhooks/status": { enabled: false, configured: false, endpoint: null, deliveries: [] },
    "DELETE /api/meraki/oauth/connection": () => { connected = false; return { revoked: false }; },
  });
  renderAt("/", [{ path: "/", element: <MerakiAdmin me={{ ...ME, role: "admin" }} /> }]);
  expect(await screen.findByText("Meraki OAuth connected.")).toBeInTheDocument();
  expect(screen.getByText(OAUTH.oauth.redirect_uri)).toBeInTheDocument();
  expect(calls.filter(c => c.method !== "GET")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Disconnect Meraki" }));
  expect(await screen.findByText(/Local connection removed/)).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Connect Meraki" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Read Meraki now" })).toBeDisabled();
  expect(calls.filter(c => c.method === "DELETE")).toHaveLength(1);
});

test("OAuth callback failure is visible and reconnect is disabled until registration is saved", async () => {
  mockApi({
    "GET /api/meraki/status": { ...OAUTH, configured: false, oauth: { configured: false, connected: false } },
    "GET /api/settings/meraki": { section: "meraki", fields: [] },
    "GET /api/meraki/webhooks/status": { enabled: false, configured: false, endpoint: null, deliveries: [] },
  });
  renderAt("/?meraki_oauth=failed", [{ path: "/", element: <MerakiAdmin me={{ ...ME, role: "admin" }} /> }]);
  expect(await screen.findByRole("alert")).toHaveTextContent("Meraki authorization was not completed");
  expect(screen.getByRole("button", { name: "Connect Meraki" })).toBeDisabled();
});
