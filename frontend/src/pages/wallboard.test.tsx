// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { mockApi, renderAt, ME } from "../test/util";
import { WallboardsAdmin } from "./admin/WallboardsAdmin";
import { span, WallboardPage } from "./Wallboard";

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

const now = new Date();
const minutesAgo = (m: number) => new Date(now.getTime() - m * 60_000).toISOString();
const BOARD = {
  name: "Service desk TV",
  generated_at: now.toISOString(),
  unassigned_alert_minutes: 30,
  totals: { open: 12, unassigned: 3, sla_at_risk: 2, sla_breached: 1, waiting_on_user: 4, new_today: 7, resolved_today: 5 },
  alerts: [
    { level: "critical", text: "1 ticket past its SLA" },
    { level: "warning", text: "2 tickets unassigned for over 30 min" },
  ],
  unassigned: [{ id: 4821, label: "#4821", subject: "VPN down for sales", priority: "critical", queue: "Network", requester: "R. Alvarez", assignee: null, created_at: minutesAgo(95), due_at: null, breached: false }],
  sla: [{ id: 4790, label: "#4790", subject: "Laptop won't boot", priority: "high", queue: "Desktop", requester: "M. Osei", assignee: "J. Park", created_at: minutesAgo(600), due_at: minutesAgo(20), breached: true }],
  queues: [{ name: "Network", open: 8, unassigned: 2, breached: 1 }, { name: "Printing", open: 0, unassigned: 0, breached: 0 }],
  people: [{ name: "J. Park", open: 5, needs_attention: 2 }],
};

function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <WallboardPage />
    </QueryClientProvider>,
  );
}

test("the board shows counts, alerts and the tickets that need someone, using the token from the fragment", async () => {
  window.location.hash = "#l8w_secret";
  const calls = mockApi({ "GET /api/public/wallboard": BOARD, "GET /api/branding": {} });
  show();
  expect(await screen.findByText("VPN down for sales")).toBeInTheDocument();
  expect(screen.getByText("1 ticket past its SLA")).toBeInTheDocument();
  const counts = screen.getByRole("region", { name: "Counts" });
  expect(within(counts).getByText("SLA breached").closest(".wb-tile")).toHaveClass("alarm");
  expect(within(counts).getByText("12")).toBeInTheDocument();
  expect(screen.getByText("Network · R. Alvarez")).toBeInTheDocument();
  expect(screen.getByText("J. Park · M. Osei")).toBeInTheDocument();
  expect(screen.getByText(/20 min over/)).toBeInTheDocument();
  // Quiet queues are counted, not listed, so the panel fits.
  expect(screen.queryByText("Printing")).not.toBeInTheDocument();
  expect(screen.getByText("1 more queue with nothing open")).toBeInTheDocument();
  // The token never goes into a URL.
  expect(calls.every((c) => !c.path.includes("l8w_"))).toBe(true);
});

test("critical VMs get a tile only when the server sends them", async () => {
  window.location.hash = "#l8w_secret";
  mockApi({ "GET /api/public/wallboard": { ...BOARD, totals: { ...BOARD.totals, critical_vms_down: 1 }, alerts: [{ level: "critical", text: "Critical VM APP01 is down" }] }, "GET /api/branding": {} });
  show();
  expect(await screen.findByText("Critical VM APP01 is down")).toBeInTheDocument();
  const counts = screen.getByRole("region", { name: "Counts" });
  expect(within(counts).getByText("Critical VMs down").closest(".wb-tile")).toHaveClass("alarm");
  expect(counts.style.getPropertyValue("--wb-tiles")).toBe("8");
});

test("no critical VM tile without Nutanix", async () => {
  window.location.hash = "#l8w_secret";
  mockApi({ "GET /api/public/wallboard": BOARD, "GET /api/branding": {} });
  show();
  const counts = await screen.findByRole("region", { name: "Counts" });
  expect(within(counts).queryByText("Critical VMs down")).not.toBeInTheDocument();
});

test("a switched-off link says so instead of showing stale numbers", async () => {
  window.location.hash = "#l8w_old";
  mockApi({ "GET /api/public/wallboard": new Response(JSON.stringify({ detail: "This wallboard link was switched off or replaced." }), { status: 401 }), "GET /api/branding": {} });
  show();
  expect(await screen.findByRole("heading", { name: "This wallboard was switched off" })).toBeInTheDocument();
});

test("without the part after # the page explains where the link comes from", () => {
  mockApi({ "GET /api/branding": {} });
  show();
  expect(screen.getByRole("heading", { name: "No wallboard link" })).toBeInTheDocument();
});

test("ages read well across a room", () => {
  expect(span(5 * 60_000)).toBe("5 min");
  expect(span(185 * 60_000)).toBe("3 h 05");
  expect(span(52 * 3_600_000)).toBe("2 d 4 h");
});

test("settings: make a wallboard for chosen queues and see its link once", async () => {
  const calls = mockApi({
    "GET /api/wallboards": [],
    "GET /api/queues": [{ id: 2, name: "Network", restricted: false, is_active: true }, { id: 3, name: "HR cases", restricted: true, is_active: true }],
    "POST /api/wallboards": { id: 1, name: "Service desk TV", prefix: "l8w_abcd", queue_ids: [3], unassigned_alert_minutes: 15, is_active: true, created_at: now.toISOString(), created_by: "Admin", last_seen_at: null, token: "l8w_abcdsecret" },
  });
  renderAt("/", [{ path: "/", element: <WallboardsAdmin me={{ ...ME, role: "admin" }} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "New wallboard" }));
  await userEvent.type(screen.getByLabelText("Name"), "Service desk TV");
  await userEvent.click(await screen.findByLabelText(/HR cases/));
  const minutes = screen.getByLabelText(/unassigned for/);
  await userEvent.clear(minutes);
  await userEvent.type(minutes, "15");
  await userEvent.click(screen.getByRole("button", { name: "Make the wallboard" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Service desk TV", queue_ids: [3], unassigned_alert_minutes: 15 }));
  expect(await screen.findByText(`${window.location.origin}/wallboard#l8w_abcdsecret`)).toBeInTheDocument();
});
