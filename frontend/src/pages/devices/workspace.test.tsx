// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { DevicePage, DevicesPage } from "./DevicesPage";

const device = {
  id: 7, name: "WS-07", dns_hostname: "ws-07.corp.example", source: "directory", directory_dn: "CN=WS-07,DC=corp,DC=example",
  os: "Windows 11", os_version: "24H2", description: "Shared workstation", enabled: true, is_active: true,
  last_logon_at: null, assigned_user: { display_name: "Sample User", email: "user@example.com" }, notes: "Original note",
  manufacturer: "Example manufacturer", model: "Workstation", serial: "SAMPLE-07", purchased_on: "2024-01-01", warranty_ends: null, replace_by: null,
  tickets: [ticket()],
};

test("device layout preserves notes, lifecycle updates and ticket navigation", async () => {
  let current = { ...device };
  const calls = mockApi({
    "GET /api/devices/7": () => current, "GET /api/devices/7/warranty": { vendor: null, checked_at: null },
    "GET /api/ipam/devices/7/addresses": [], "GET /api/action1/devices/7": null,
    "PATCH /api/devices/7": ({ body }: Call) => (current = { ...current, ...(body as object) }),
  });
  renderAt("/devices/7", [{ path: "/devices/:deviceId", element: <DevicePage me={ME} /> }]);
  expect(await screen.findByRole("heading", { name: "WS-07" })).toBeInTheDocument();
  const context = screen.getByRole("complementary", { name: "Device service context" });
  expect(within(context).getByRole("link", { name: /#4821/ })).toHaveAttribute("href", "/tickets/4821");
  expect(screen.getByRole("link", { name: "Go to notes" })).toHaveAttribute("href", "#device-notes");
  const notes = screen.getByRole("textbox", { name: /Notes/ });
  await userEvent.clear(notes); await userEvent.type(notes, "Checked cabling");
  await userEvent.click(screen.getByRole("button", { name: "Save notes" }));
  await waitFor(() => expect(calls.some(c => c.method === "PATCH" && (c.body as any).notes === "Checked cabling")).toBe(true));
  await userEvent.clear(screen.getByLabelText("Model")); await userEvent.type(screen.getByLabelText("Model"), "Updated workstation");
  await userEvent.click(within(screen.getByRole("form", { name: "Lifecycle" })).getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(calls.some(c => c.method === "PATCH" && (c.body as any).model === "Updated workstation")).toBe(true));
  expect(screen.queryByRole("button", { name: "Remove this device" })).not.toBeInTheDocument();
});

test("optional patching stays hidden and manual removal retains its permission gate", async () => {
  mockApi({ "GET /api/devices/7": { ...device, source: "manual" }, "GET /api/devices/7/warranty": {}, "GET /api/ipam/devices/7/addresses": [] });
  renderAt("/devices/7", [{ path: "/devices/:deviceId", element: <DevicePage me={{ ...ME, features: [], permissions: [...ME.permissions, "devices_delete"] }} /> }]);
  expect(await screen.findByRole("button", { name: "Remove this device" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Patching" })).not.toBeInTheDocument();
});

test("lifecycle summary retains forecast and replacement device links", async () => {
  mockApi({ "GET /api/devices/lifecycle": { life_years: 5, warn_days: 90, unknown: 4, overdue: 2, warranty_soon: [device], warranty_ended: [], forecast: [["2027 Q1", 3]], replace_soon: [{ device, replace_due: "2027-01-01" }] } });
  renderAt("/devices?view=lifecycle", [{ path: "/devices", element: <DevicesPage me={ME} /> }]);
  expect(await screen.findByLabelText("Lifecycle summary")).toHaveTextContent("Replacement overdue2");
  expect(screen.getByText("2027 Q1")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "WS-07" }).every(a => a.getAttribute("href") === "/devices/7")).toBe(true);
});
