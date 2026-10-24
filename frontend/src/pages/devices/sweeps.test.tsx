// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { DevicesPage } from "./DevicesPage";

afterEach(() => vi.unstubAllGlobals());

const device = { id: 4, name: "WS-OLD", dns_hostname: null, source: "directory", directory_dn: null, os: null, os_version: null, description: null, enabled: true,
  is_active: true, last_logon_at: "2026-04-01T00:00:00Z", assigned_user: null, notes: "", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };

test("sweeps: stale devices and unknown ones, as tabs on Devices", async () => {
  mockApi({
    "GET /api/devices": { items: [device], total: 1 },
    "GET /api/devices/sweep": {
      stale_days: 90, recent_days: 7,
      stale: [{ device, last_seen_at: "2026-04-01T00:00:00Z", ad_logon_at: "2026-04-01T00:00:00Z", action1_at: null, network_at: null }],
      unknown: [{ kind: "address", name: "mystery-cam", address: "10.9.0.6", mac: "aa:bb:cc:00:00:06", last_seen_at: "2026-10-19T00:00:00Z", ip_address_id: 1, finding_id: null, patch_id: null, notes: ["documented as static"] }],
    },
  });
  renderAt("/devices", [{ path: "/devices", element: <DevicesPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Stale" }));
  const stale = await screen.findByRole("table");
  expect(within(stale).getByRole("link", { name: "WS-OLD" })).toHaveAttribute("href", "/devices/4");
  await userEvent.click(screen.getByRole("tab", { name: "Unknown on the network" }));
  expect(await screen.findByText("mystery-cam")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "10.9.0.6" })).toHaveAttribute("href", "/ipam/addresses?q=10.9.0.6");
  expect(screen.getByText(/Seen by scans/)).toBeInTheDocument();
});
