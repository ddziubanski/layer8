// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen } from "@testing-library/react";
import { mockApi, renderAt, ticket } from "./util";
import { TicketNetworkContext } from "../pages/ticket/TicketNetworkContext";

test("network drawer reads linked device records without launching a scan", async () => {
  const calls = mockApi({
    "GET /api/tickets/4821/devices": [{ device: { id: 12, name: "LAP-012" } }],
    "GET /api/ipam/devices/12/addresses": [],
  });
  renderAt("/", [{ path: "/", element: <TicketNetworkContext ticket={ticket()} includeComputer={false} includeDevices /> }]);
  expect(await screen.findByRole("link", { name: "LAP-012" })).toHaveAttribute("href", "/devices/12");
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("hiding both device panels avoids their requests", async () => {
  const calls = mockApi({});
  renderAt("/", [{ path: "/", element: <TicketNetworkContext ticket={ticket()} includeComputer={false} includeDevices={false} /> }]);
  expect(await screen.findByText("Link a computer or device to see network context.")).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});
