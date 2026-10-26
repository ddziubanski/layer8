// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const device = (id: number, name: string, over = {}) => ({
  id, name, dns_hostname: `${name.toLowerCase()}.corp.example`, source: "directory", directory_dn: null, os: "Windows 11", os_version: null,
  description: null, enabled: true, is_active: true, last_logon_at: null, assigned_user: null, notes: "", created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z", ...over,
});

test("link the requester's device in one click, find another, unlink", async () => {
  const t = ticket();
  const WS = device(1, "WS-0142");
  const SW = device(2, "SW-CORE-1", { os: "Cisco Meraki MS", source: "manual" });
  let linked: object[] = [];
  const link = (d: object) => ({ device: d, linked_at: "2026-10-19T00:00:00Z", linked_by: null });
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: () => linked,
    "GET /api/devices": ({ path }: Call) => (path.includes("assigned_user_id") ? { items: [WS], total: 1 } : { items: [SW, WS], total: 2 }),
    [`POST /api/tickets/${t.id}/devices`]: ({ body }: Call) => {
      linked = [...linked, link((body as { device_id: number }).device_id === 1 ? WS : SW)];
      return linked;
    },
    [`DELETE /api/tickets/${t.id}/devices/1`]: () => {
      linked = linked.filter((l) => (l as { device: { id: number } }).device.id !== 1);
      return linked;
    },
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Tools" }));
  await userEvent.click(within(screen.getByRole("group", { name: "Ticket tools" })).getByRole("button", { name: /Devices/ }));
  const card = await screen.findByRole("region", { name: "Devices" });
  await userEvent.click(await within(card).findByRole("button", { name: "+ WS-0142" }));
  expect(await within(card).findByRole("link", { name: "WS-0142" })).toHaveAttribute("href", "/devices/1");
  expect(within(card).queryByRole("button", { name: "+ WS-0142" })).not.toBeInTheDocument();

  await userEvent.type(within(card).getByRole("combobox", { name: "Link a device" }), "eos");
  await userEvent.click(await within(card).findByRole("option", { name: /SW-CORE-1/ }));
  expect(await within(card).findByRole("link", { name: "SW-CORE-1" })).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([{ device_id: 1 }, { device_id: 2 }]));

  await userEvent.click(within(card).getByRole("button", { name: "Unlink WS-0142" }));
  await waitFor(() => expect(within(card).queryByRole("link", { name: "WS-0142" })).not.toBeInTheDocument());
});

test("a linked device shows its IP addresses; a matching host name links in one click", async () => {
  const t = ticket();
  const WS = device(1, "WS-0142");
  const ip = (id: number, address: string, over = {}) => ({
    id, address, hostname: "ws-0142.corp.example", mac: "", kind: "reserved", device: null, system: null, notes: "", source: "manual", first_seen_at: null,
    last_seen_at: null, updated_at: "2026-10-20T00:00:00Z", subnet: { id: 1, network: "10.1.20.0/24", name: "Staff", vlan: { id: 2, number: 20, name: "Staff", site: "" } }, ...over,
  });
  let linkedIp = false;
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [{ device: WS, linked_at: "2026-10-19T00:00:00Z", linked_by: null }],
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/ipam/devices/1/addresses": () => [{ address: ip(7, "10.1.20.12"), linked: true }, { address: ip(8, "10.1.20.13"), linked: linkedIp }],
    "POST /api/ipam/addresses/8/link-device/1": () => ((linkedIp = true), ip(8, "10.1.20.13")),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Tools" }));
  await userEvent.click(within(screen.getByRole("group", { name: "Ticket tools" })).getByRole("button", { name: /Devices/ }));
  const card = await screen.findByRole("region", { name: "Devices" });
  const ips = await within(card).findByRole("list", { name: "IP addresses" });
  expect(within(ips).getByRole("link", { name: "10.1.20.12" })).toHaveAttribute("href", "/ipam/addresses?q=10.1.20.12");
  expect(within(ips).getAllByText(/10.1.20.0\/24 Staff · VLAN 20/)).toHaveLength(2);
  await userEvent.click(within(ips).getByRole("button", { name: "Link" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/ipam/addresses/8/link-device/1")).toBe(true));
  await waitFor(() => expect(within(ips).queryByRole("button", { name: "Link" })).not.toBeInTheDocument());
});

test("a linked device shows its patch state from Action1", async () => {
  const t = ticket();
  const WS = device(1, "WS-0142");
  mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [{ device: WS, linked_at: "2026-10-19T00:00:00Z", linked_by: null }],
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/action1/devices/1": {
      id: 1, action1_id: "a1", name: "WS-0142", device_id: 1, status: "Connected", os: "Windows 11", agent_version: "5.3", user: "", last_seen_at: null,
      last_boot_at: null, missing_critical: 2, missing_other: 4, reboot_required: true, update_status: "", vulnerability_status: "", vulns_critical: 0, vulns_other: 0, organization: "", critical_since: null,
      reboot_since: null, ticket_id: 77, in_action1: true, synced_at: "2026-10-20T00:00:00Z",
    },
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Tools" }));
  await userEvent.click(within(screen.getByRole("group", { name: "Ticket tools" })).getByRole("button", { name: /Devices/ }));
  const card = await screen.findByRole("region", { name: "Devices" });
  const patching = await within(card).findByLabelText("Patching");
  expect(patching).toHaveTextContent("2 critical updates missing · reboot pending");
  expect(within(patching).getByRole("link", { name: "patching ticket" })).toHaveAttribute("href", "/tickets/77");
});
