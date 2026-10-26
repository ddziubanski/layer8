// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { DevicesCard } from "./DevicesCard";
import { TicketNetworkContext } from "./TicketNetworkContext";

afterEach(() => vi.unstubAllGlobals());
const target = { kind: "unifi_device", source_id: "console:site-a", key: "ap-1", source_label: "UniFi", name: "Lobby AP", address: "192.0.2.10", mac: "aa:bb:cc:00:00:01", source_name: "Example office", details: "Example AP · online", observed_at: "2026-10-26T00:00:00Z", stale: false };
const linked = { id: 1, target, current: target, linked_at: "2026-10-26T00:00:00Z" };
const device = { id: 5, name: "PC-0042", source: "directory", is_active: true, assigned_user: null };

function setup(extra: Record<string, unknown> = {}) {
  const calls = mockApi({
    "GET /api/auth/me": ME,
    "GET /api/tickets/4821/devices": [{ device }],
    "GET /api/tickets/4821/network-links": [],
    "GET /api/tickets/4821/device-candidates": [target],
    "GET /api/ipam/devices/5/addresses": [],
    "GET /api/action1/devices/5": null,
    "GET /api/devices": { items: [], total: 0 },
    "POST /api/tickets/4821/network-links": [linked],
    "DELETE /api/tickets/4821/network-links/1": [],
    ...extra,
  });
  renderAt("/", [{ path: "/", element: <DevicesCard ticket={ticket()} onChanged={() => {}} /> }]);
  return calls;
}

test("one device search links UniFi equipment by IP and displays source pills alongside AD assets", async () => {
  const calls = setup();
  expect(await screen.findByText("PC-0042")).toBeInTheDocument();
  expect(screen.getByText("Active Directory")).toBeInTheDocument();
  await userEvent.type(screen.getByRole("combobox", { name: "Link a device" }), "192.0.2.10");
  const option = await screen.findByRole("option", { name: /Lobby AP/ });
  expect(within(option).getByText("UniFi")).toBeInTheDocument();
  expect(option).toHaveTextContent("Example office");
  await userEvent.click(option);
  await waitFor(() => expect(calls.filter(c => c.method === "POST")).toHaveLength(1));
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ kind: "unifi_device", source_id: "console:site-a", key: "ap-1" });
  expect(await screen.findByRole("button", { name: "Unlink Lobby AP" })).toBeInTheDocument();
  expect(screen.getByText("UniFi")).toHaveAttribute("title", "Source: UniFi");
  expect(screen.getAllByRole("region", { name: "Devices" })).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Unlink Lobby AP" }));
  await waitFor(() => expect(screen.queryByText("Lobby AP")).not.toBeInTheDocument());
});

test("IPAM scans use the same picker and link API", async () => {
  const scan = { ...target, kind: "ip_address", source_id: "", key: "19", name: "Printer", source_label: "IPAM scan" };
  const calls = setup({ "GET /api/tickets/4821/device-candidates": [scan], "POST /api/tickets/4821/network-links": [{ ...linked, target: scan, current: scan }] });
  await userEvent.type(screen.getByRole("combobox", { name: "Link a device" }), "Printer");
  await screen.findByRole("option", { name: /Printer/ });
  await userEvent.keyboard("{ArrowDown}{Enter}");
  await waitFor(() => expect(calls.some(c => c.method === "POST")).toBe(true));
  expect(await screen.findByText("IPAM scan")).toBeInTheDocument();
});

test("failed link keeps the device unlinked and explains the error", async () => {
  setup({ "POST /api/tickets/4821/network-links": new Response(JSON.stringify({ detail: "Record is no longer available" }), { status: 404 }) });
  await userEvent.type(screen.getByRole("combobox", { name: "Link a device" }), "Lobby");
  await userEvent.click(await screen.findByRole("option", { name: /Lobby AP/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Record is no longer available");
  expect(screen.queryByRole("button", { name: "Unlink Lobby AP" })).not.toBeInTheDocument();
});

test("missing scan retains the saved identity, and changed IP shows original address", async () => {
  setup({ "GET /api/tickets/4821/network-links": [{ ...linked, current: null }] });
  expect(await screen.findByText(/Saved when linked/)).toBeInTheDocument();
  expect(screen.getByText("Lobby AP")).toBeInTheDocument();
});

test("network context includes explicitly linked devices without an AD computer or IPAM asset", async () => {
  const calls = mockApi({ "GET /api/tickets/4821/devices": [], "GET /api/tickets/4821/network-links": [linked] });
  renderAt("/", [{ path: "/", element: <TicketNetworkContext ticket={ticket()} includeComputer={false} includeDevices /> }]);
  expect(await screen.findByText("Lobby AP")).toBeInTheDocument();
  expect(calls.every((c: Call) => c.method === "GET")).toBe(true);
});
