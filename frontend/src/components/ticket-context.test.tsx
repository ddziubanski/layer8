// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { TicketContext } from "../pages/ticket/TicketContext";
import { mockApi, renderAt } from "../test/util";

test("shows requester account warnings, device live state, and nearby outages, problems and changes", async () => {
  mockApi({
    "GET /api/tickets/4821/context": {
      account: [
        { kind: "locked", label: "Locked in Active Directory", tone: "bad", at: null },
        { kind: "expiring", label: "Active Directory account expires soon", tone: "warn", at: "2026-11-05T12:00:00Z" },
      ],
      devices: [
        {
          id: 14,
          name: "WS-0142",
          role: "computer",
          online: true,
          last_seen_at: "2026-10-31T23:55:00Z",
          switch_port: "SW-2F port 12 · Staff",
          subnet: "10.20.0.0/24 (Floor 2)",
          site: "HQ",
          pending_reboot: true,
          critical_patches: 2,
          ad_disabled: false,
        },
      ],
      outages: [
        {
          provider_label: "UniFi",
          name: "SW-2F",
          kind: "switch",
          down_since: "2026-10-31T23:30:00Z",
          scope: "Switch SW-2F",
          ticket_id: 4820,
          ticket_label: "#4820",
        },
      ],
      problems: [
        {
          id: 3,
          label: "PRB-00003",
          title: "Floor 2 DHCP helper",
          status: "known_error",
          workaround: "Use Guest Wi-Fi while the relay is fixed.",
          scope: "Subnet 10.20.0.0/24 (Floor 2)",
          linked: false,
        },
      ],
      hidden_problems: 1,
      changes: [
        {
          id: 12,
          label: "CHG-00012",
          title: "Upgrade Floor 2 distribution config",
          status: "completed",
          at: "2026-10-31T21:00:00Z",
          scope: "Subnet 10.20.0.0/24 (Floor 2)",
        },
      ],
    },
  });

  renderAt("/", [{ path: "/", element: <TicketContext ticketId={4821} /> }]);

  expect(await screen.findByRole("region", { name: "Around this ticket" })).toBeInTheDocument();
  expect(screen.getByText(/Locked in Active Directory/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "WS-0142" })).toHaveAttribute("href", "/devices/14");
  expect(screen.getByText("Online")).toBeInTheDocument();
  expect(screen.getByText("SW-2F port 12 · Staff")).toBeInTheDocument();
  expect(screen.getByText("Pending reboot")).toBeInTheDocument();
  expect(screen.getByText("2 critical updates missing")).toBeInTheDocument();
  expect(screen.getByText("UniFi SW-2F")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "#4820" })).toHaveAttribute("href", "/tickets/4820");
  expect(screen.getByRole("link", { name: "PRB-00003 Floor 2 DHCP helper" })).toHaveAttribute("href", "/problems/3");
  expect(screen.getByText(/Use Guest Wi-Fi while the relay is fixed/)).toBeInTheDocument();
  expect(screen.getByText(/1 open problem nearby cannot be shown/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "CHG-00012 Upgrade Floor 2 distribution config" })).toHaveAttribute("href", "/changes/12");
});

test("stays hidden when nothing is wrong or linked around the ticket", async () => {
  mockApi({
    "GET /api/tickets/4821/context": {
      account: [],
      devices: [],
      outages: [],
      problems: [],
      hidden_problems: 0,
      changes: [],
    },
  });

  const { container } = renderAt("/", [{ path: "/", element: <TicketContext ticketId={4821} /> }]);
  expect(container.querySelector(".ticket-context")).toBeNull();
});
