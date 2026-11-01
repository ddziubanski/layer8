// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { ChangePage } from "./ChangesPage";

afterEach(() => vi.unstubAllGlobals());

const ADMIN = { ...ME, id: 7, role: "admin" as const };
const CHANGE = {
  id: 12, label: "CHG-00012", title: "Core switch firmware 17.1", kind: "normal", status: "awaiting_approval", risk: 6, risk_level: "high",
  planned_start: "2026-11-01T22:00:00Z", planned_end: "2026-11-01T23:30:00Z", owner: { id: 1, display_name: "J. Park", email: "jpark@corp.local" },
  approved_at: null, updated_at: "2026-10-20T00:00:00Z", template_id: null, description: "", implementation_plan: "One member at a time", rollback_plan: "Boot the previous image",
  test_plan: "", impact: 3, likelihood: 2, requested_by: { id: 1, display_name: "J. Park", email: "jpark@corp.local" }, approved_by: null, outcome: "",
  started_at: null, finished_at: null, created_at: "2026-10-20T00:00:00Z", tickets: [], devices: [], systems: [],
  events: [{ id: 1, at: "2026-10-20T00:00:00Z", actor: null, type: "submitted", note: "" }], needs_approval: true, can_approve: true, can_edit: true, conflicts: [],
};

test("new change: risk follows impact and likelihood; the rollback plan is asked for", async () => {
  const calls = mockApi({
    "GET /api/change-templates": [],
    "GET /api/users": [{ id: 1, display_name: "J. Park", email: "jpark@corp.local", role: "agent", is_active: true }],
    "GET /api/systems": [],
    "POST /api/changes": ({ body }: Call) => ({ ...CHANGE, ...(body as object), id: 13, status: "draft" }),
  });
  renderAt("/changes/new", [{ path: "/changes/:changeId", element: <ChangePage me={ME} /> }]);
  const form = await screen.findByRole("form", { name: "Change" });
  await userEvent.type(within(form).getByLabelText("Title"), "Firewall rule for the VPN");
  await userEvent.selectOptions(within(form).getByLabelText("Impact if it goes wrong"), "3");
  await userEvent.selectOptions(within(form).getByLabelText("Likelihood it goes wrong"), "2");
  expect(within(form).getByText("High risk")).toBeInTheDocument();
  await userEvent.type(within(form).getByLabelText(/Rollback plan/), "Remove the rule");
  await userEvent.click(within(form).getByRole("button", { name: "Create change" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ title: "Firewall rule for the VPN", impact: 3, likelihood: 2, rollback_plan: "Remove the rule" }));
});

test("an approver approves, with a note for the record", async () => {
  const calls = mockApi({
    "GET /api/changes/12": CHANGE,
    "GET /api/change-templates": [],
    "GET /api/users": [],
    "GET /api/systems": [],
    "POST /api/changes/12/actions": ({ body }: Call) => ({ ...CHANGE, status: "approved", can_approve: false, can_edit: false, approved_at: "2026-10-20T01:00:00Z", ...(body as object) }),
  });
  renderAt("/changes/12", [{ path: "/changes/:changeId", element: <ChangePage me={ADMIN} /> }]);
  const bar = await screen.findByRole("region", { name: "Next steps" });
  expect(within(bar).getByText("Waiting for your approval.")).toBeInTheDocument();
  await userEvent.type(within(bar).getByLabelText("Note"), "OK in the window");
  await userEvent.click(within(bar).getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")!.body).toEqual({ action: "approve", note: "OK in the window" }));
  expect(await within(bar).findByRole("button", { name: "Start" })).toBeInTheDocument();
});

test("overlapping changes are flagged, with what they share", async () => {
  mockApi({
    "GET /api/changes/12": {
      ...CHANGE,
      conflicts: [{ change: { ...CHANGE, id: 14, label: "CHG-00014", title: "Re-cable the 2nd floor stack", status: "approved" }, shared: ["SW-CORE-1"] }],
    },
    "GET /api/change-templates": [],
    "GET /api/users": [],
    "GET /api/systems": [],
  });
  renderAt("/changes/12", [{ path: "/changes/:changeId", element: <ChangePage me={ADMIN} /> }]);
  const warn = await screen.findByRole("region", { name: "Overlapping changes" });
  expect(within(warn).getByRole("link", { name: "CHG-00014 Re-cable the 2nd floor stack" })).toHaveAttribute("href", "/changes/14");
  expect(warn).toHaveTextContent("also touches SW-CORE-1");
});

test("calendar: a month of change windows", async () => {
  const { ChangesPage } = await import("./ChangesPage");
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 15, 22, 0).toISOString();
  const calls = mockApi({
    "GET /api/changes": [{ ...CHANGE, planned_start: start, title: "Firmware window" }],
  });
  renderAt("/changes", [{ path: "/changes", element: <ChangesPage /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Calendar" }));
  const cal = await screen.findByRole("grid", { name: "Change calendar" });
  const day = within(cal).getByRole("gridcell", { name: new Date(now.getFullYear(), now.getMonth(), 15).toDateString() });
  expect(await within(day).findByRole("link", { name: /Firmware window/ })).toHaveAttribute("href", "/changes/12");
  expect(calls.some((c) => c.path.includes("starts_from="))).toBe(true);
});

test("post-change review: suggests follow-up tickets and lets a technician confirm and link them", async () => {
  const followUp = { id: 105, label: "#105", subject: "2nd floor phones offline", status: "open" as const, created_at: "2026-11-01T23:45:00Z" };
  const calls = mockApi({
    "GET /api/changes/12": {
      ...CHANGE,
      status: "completed",
      can_approve: false,
      can_edit: false,
      follow_up_tickets: [],
      suggested_tickets: [{ ticket: followUp, scope: "Switch SW-CORE-1" }],
    },
    "GET /api/change-templates": [],
    "GET /api/users": [],
    "GET /api/systems": [],
    "POST /api/changes/12/tickets": () => ({
      ...CHANGE,
      status: "completed",
      can_approve: false,
      can_edit: false,
      tickets: [followUp],
      follow_up_tickets: [followUp],
      suggested_tickets: [],
    }),
  });
  renderAt("/changes/12", [{ path: "/changes/:changeId", element: <ChangePage me={ADMIN} /> }]);
  const review = await screen.findByRole("region", { name: "Post-change review" });
  expect(review).toHaveTextContent("0 tickets followed this change");
  expect(review).toHaveTextContent("Completed without follow-up incidents (counts as successful)");
  expect(review).toHaveTextContent("Possibly related to CHG-00012");
  expect(review).toHaveTextContent("Switch SW-CORE-1");
  await userEvent.click(within(review).getByRole("button", { name: "Link ticket" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/changes/12/tickets")!.body).toEqual({ ticket_id: 105 }));
  expect(await within(review).findByText(/1 ticket followed this change/)).toBeInTheDocument();
  expect(review).toHaveTextContent("Completed with follow-up incidents (counts against change success rate)");
});

test("ticket changes card: flags a possibly related change and links it on confirmation", async () => {
  const { ChangesCard } = await import("../ticket/ChangesCard");
  const { ticket } = await import("../../test/util");
  const calls = mockApi({
    "GET /api/tickets/101/changes": [],
    "GET /api/tickets/101/change-suggestions": [{ change: { ...CHANGE, status: "completed" }, scope: "Switch SW-CORE-1" }],
    "POST /api/changes/12/tickets": () => ({ ...CHANGE, status: "completed" }),
  });
  renderAt("/tickets/101", [{ path: "/tickets/:ticketId", element: <ChangesCard ticket={ticket({ id: 101 })} /> }]);
  const card = await screen.findByRole("region", { name: "Changes" });
  expect(await within(card).findByText("Possibly related to CHG-00012")).toBeInTheDocument();
  expect(card).toHaveTextContent("Switch SW-CORE-1");
  await userEvent.click(within(card).getByRole("button", { name: "Link" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/changes/12/tickets")!.body).toEqual({ ticket_id: 101 }));
});
