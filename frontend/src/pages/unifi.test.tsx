// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NetworkContext } from "../components/NetworkContext";
import { mockApi, renderAt, ticket } from "../test/util";
import { UnifiPage } from "./ipam/UnifiPage";
import { GuestWifiCard } from "./ticket/GuestWifiCard";

afterEach(() => vi.unstubAllGlobals());

const NOW = new Date().toISOString();
const OBS = { provider: "unifi", provider_label: "UniFi", source_id: "c1:s1", source_name: "Head office", mac: "bb:bb:bb:00:00:01", address: "10.0.1.20", hostname: "reception-laptop", network: "Staff LAN", uplink_name: "Lobby AP", port: "", ssid: "Staff", connection: "wireless", last_seen_at: NOW, observed_at: NOW, stale: false };

test("IP addresses > UniFi lists equipment with offline first and searches clients", async () => {
  const calls = mockApi({
    "GET /api/unifi/devices": [
      { source_id: "c1:s1", source_name: "Head office", device_key: "ap", name: "Lobby AP", model: "U6-Pro", kind: "ap", mac: "aa:aa:aa:00:00:02", address: "10.0.0.2", state: "offline", firmware: "6.6.77", observed_at: NOW },
      { source_id: "c1:s1", source_name: "Head office", device_key: "gw", name: "Gateway", model: "UDM-Pro", kind: "gateway", mac: "aa:aa:aa:00:00:01", address: "10.0.0.1", state: "online", firmware: "4.0.6", observed_at: NOW },
    ],
    "GET /api/unifi/clients": [OBS],
  });
  renderAt("/", [{ path: "/", element: <UnifiPage /> }]);
  expect(await screen.findByText("1 offline")).toBeInTheDocument();
  expect(screen.getByText("Access point")).toBeInTheDocument();
  expect(await screen.findByText("reception-laptop")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Search UniFi clients"), "lobby");
  await waitFor(() => expect(calls.some((c) => c.path.includes("/api/unifi/clients?q=lobby"))).toBe(true));
});

test("a UniFi observation shows beside a documented record", () => {
  renderAt("/", [{ path: "/", element: <NetworkContext observations={[OBS]} /> }]);
  expect(screen.getByLabelText("UniFi observation")).toHaveTextContent("UniFi · Head office");
  expect(screen.getByLabelText("UniFi observation")).toHaveTextContent("Lobby AP · Staff LAN · SSID Staff · wireless");
});

test("guest Wi-Fi: make a voucher for the ticket and show its code", async () => {
  const calls = mockApi({
    "GET /api/unifi/sites": [{ source_id: "c1:s1", name: "Head office" }],
    "POST /api/unifi/tickets/4821/vouchers": { codes: ["12345-67890"], minutes: 1440, site: "Head office" },
  });
  renderAt("/", [{ path: "/", element: <GuestWifiCard ticket={ticket()} onChanged={() => undefined} /> }]);
  await userEvent.selectOptions(await screen.findByLabelText("Valid for"), "1440");
  await userEvent.click(screen.getByRole("button", { name: "Make a guest voucher" }));
  expect(await screen.findByText("12345-67890")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ source_id: "c1:s1", minutes: 1440, guests: 1 });
});
