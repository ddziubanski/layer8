// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderAt, ticket } from "../test/util";
import { DashboardPage } from "./DashboardPage";

const counts = { open: 17, unassigned: 4, sla_at_risk: 2, waiting_on_user: 3, from_email: 9, from_portal: 8 };
test("dashboard defaults to my workload and links preserve explicit scope and queue", async () => {
  const calls = mockApi({ "GET /api/queues": [{ id: 2, name: "Network" }], "GET /api/tickets/views": counts, "GET /api/tickets": { items: [ticket()], total: 1 } });
  renderAt("/dashboard", [{ path: "/dashboard", element: <DashboardPage /> }]);
  expect(await screen.findByText("17")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open tickets/ })).toHaveAttribute("href", "/?scope=mine&queue=&view=open");
  expect(screen.getByRole("link", { name: /Switch port flapping/ })).toHaveAttribute("href", "/tickets/4821");
  await userEvent.selectOptions(screen.getByLabelText("Workload"), "groups");
  await userEvent.selectOptions(screen.getByLabelText("Queue"), "2");
  await waitFor(() => expect(calls.some(c => c.path.includes("/api/tickets/views?scope=groups&queue_id=2"))).toBe(true));
  expect(screen.getByRole("link", { name: /Open tickets/ })).toHaveAttribute("href", "/?scope=groups&queue=2&view=open");
});

test("API failures offer retry rather than displaying zero workload", async () => {
  mockApi({ "GET /api/queues": [], "GET /api/tickets/views": new Response('{"detail":"unavailable"}', { status: 503 }), "GET /api/tickets": new Response('{}', { status: 503 }) });
  renderAt("/dashboard", [{ path: "/dashboard", element: <DashboardPage /> }]);
  expect(await screen.findByRole("button", { name: "Retry counts" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Retry attention list" })).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

test("empty attention list is explicit, while counts retain server values", async () => {
  mockApi({ "GET /api/queues": [], "GET /api/tickets/views": { ...counts, sla_at_risk: 0 }, "GET /api/tickets": { items: [], total: 0 } });
  renderAt("/dashboard?scope=all", [{ path: "/dashboard", element: <DashboardPage /> }]);
  expect(await screen.findByText("No SLA-risk tickets in this workload.")).toBeInTheDocument();
  expect(screen.getByLabelText("Workload")).toHaveValue("all");
});
