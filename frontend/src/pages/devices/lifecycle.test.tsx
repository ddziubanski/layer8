// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { DevicePage, DevicesPage } from "./DevicesPage";

afterEach(() => vi.unstubAllGlobals());

const device = {
  id: 4, name: "WS-0142", dns_hostname: null, source: "directory", directory_dn: "CN=WS-0142", os: null, os_version: null, description: null, enabled: true,
  is_active: true, last_logon_at: null, assigned_user: null, notes: "", manufacturer: "", model: "", serial: "", purchased_on: null, warranty_ends: null,
  replace_by: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", tickets: [],
};

test("lifecycle tab: warranties, replacements due and the forecast", async () => {
  mockApi({
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/devices/lifecycle": {
      life_years: 5, warn_days: 60, warranty_soon: [{ ...device, warranty_ends: "2026-11-20", model: "OptiPlex 7010" }], warranty_ended: [],
      replace_soon: [{ device: { ...device, manufacturer: "Dell", model: "OptiPlex 7010", purchased_on: "2021-11-01" }, replace_due: "2026-11-01" }],
      forecast: [["2026 Q4", 1], ["2027 Q1", 3]], overdue: 2, unknown: 7,
    },
  });
  renderAt("/devices?view=lifecycle", [{ path: "/devices", element: <DevicesPage me={ME} /> }]);
  const soon = await screen.findByRole("region", { name: "Warranty ending" });
  expect(within(soon).getByRole("link", { name: "WS-0142" })).toHaveAttribute("href", "/devices/4");
  const forecast = screen.getByRole("region", { name: "Replacement forecast" });
  expect(forecast).toHaveTextContent("2 overdue");
  expect(forecast).toHaveTextContent("2027 Q13");
  expect(screen.getByRole("region", { name: "Due within a year" })).toHaveTextContent("Dell OptiPlex 7010");
  expect(screen.getByText(/7 device\(s\) have no purchase/)).toBeInTheDocument();
});

test("a device's lifecycle details are saved by technicians", async () => {
  const calls = mockApi({
    "GET /api/devices/4": device,
    "GET /api/ipam/devices/4/addresses": [],
    "GET /api/action1/devices/4": null,
    "PATCH /api/devices/4": ({ body }: Call) => ({ ...device, ...(body as object) }),
  });
  renderAt("/devices/4", [{ path: "/devices/:deviceId", element: <DevicePage me={ME} /> }]);
  const card = await screen.findByRole("form", { name: "Lifecycle" });
  await userEvent.type(within(card).getByLabelText("Model"), "OptiPlex 7010");
  await userEvent.type(within(card).getByLabelText("Warranty ends"), "2027-03-01");
  await userEvent.click(within(card).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
      manufacturer: "", model: "OptiPlex 7010", serial: "", purchased_on: null, warranty_ends: "2027-03-01", replace_by: null,
    }),
  );
});

test("warranty from the maker: look up, and take its dates when they differ", async () => {
  const dell = { ...device, manufacturer: "Dell Inc.", serial: "ABC1234", warranty_ends: "2026-03-10" };
  const answer = {
    vendor: "dell", available: true, reason: "", serial: "ABC1234", checked_at: "2026-10-21T08:00:00Z", error: null, product: "LATITUDE 5440",
    shipped_on: "2023-03-10", purchased_on: null, warranty_ends: "2028-03-10", differs: true,
    entitlements: [{ name: "ProSupport Plus", type: "Extended", start: "2026-03-10", end: "2028-03-10" }],
  };
  let looked = false;
  const calls = mockApi({
    "GET /api/devices/4": dell,
    "GET /api/ipam/devices/4/addresses": [],
    "GET /api/action1/devices/4": null,
    "GET /api/devices/4/warranty": () => (looked ? answer : { vendor: "dell", available: true, reason: "", entitlements: [], differs: false }),
    "POST /api/devices/4/warranty/lookup": () => ((looked = true), answer),
    "POST /api/devices/4/warranty/apply": { ...answer, differs: false },
  });
  renderAt("/devices/4", [{ path: "/devices/:deviceId", element: <DevicePage me={ME} /> }]);
  const card = await screen.findByRole("region", { name: "Warranty from the maker" });
  expect(within(card).getByText(/Not looked up yet/)).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Look up now" }));
  expect(await within(card).findByText("ProSupport Plus")).toBeInTheDocument();
  expect(within(card).getByText("2028-03-10", { selector: "strong" })).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Use Dell's dates" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/devices/4/warranty/apply")).toBe(true));
});
