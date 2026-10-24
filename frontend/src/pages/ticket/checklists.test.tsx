// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const LEAVER = {
  id: 3, name: "Leaver", kind: "offboarding", description: "When someone leaves", is_active: true, updated_at: "2026-10-20T00:00:00Z",
  items: [
    { title: "Collect laptop", details: "", queue_id: null, assignee_id: null, offset_days: 0, action: null },
    { title: "Disable account", details: "", queue_id: null, assignee_id: null, offset_days: 1, action: "disable_ad_account" },
  ],
};

function page(extra: Record<string, unknown>) {
  const t = ticket();
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    "GET /api/checklist-templates": [LEAVER],
    "GET /api/users": [{ id: 9, display_name: "Pat Lee", email: "plee@corp.example", role: "requester", is_active: true }],
    ...extra,
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  return { t, calls };
}

test("start an offboarding checklist: preview the steps with their dates, then start", async () => {
  const t = ticket();
  const { calls } = page({
    [`GET /api/tickets/${t.id}/checklists`]: [],
    [`POST /api/tickets/${t.id}/checklists`]: { id: 1, name: "Leaver", kind: "offboarding", for_name: "Pat Lee", for_user_id: 9, key_date: "2026-10-30", created_at: "2026-10-20T00:00:00Z", items: [] },
  });
  const card = await screen.findByRole("region", { name: "Checklists" });
  await userEvent.click(within(card).getByRole("button", { name: "Start a checklist…" }));
  const form = within(card).getByRole("form", { name: "Start a checklist" });
  await userEvent.selectOptions(await within(form).findByLabelText("Checklist"), await within(form).findByRole("option", { name: "Leaver (2 steps)" }));
  // An offboarding checklist counts from the last day.
  await userEvent.type(within(form).getByLabelText(/Last day/), "2026-10-30");
  const steps = within(form).getByRole("list", { name: "Steps it makes" });
  expect(steps).toHaveTextContent("Disable account · due " + new Date(2026, 9, 31).toLocaleDateString() + " · disables the AD account that day");
  await userEvent.type(within(form).getByRole("combobox", { name: "For (someone with an account)" }), "pat");
  await userEvent.click(await within(form).findByRole("option", { name: /Pat Lee/ }));
  await userEvent.click(within(form).getByRole("button", { name: "Start: make 2 child ticket(s)" }));
  await waitFor(() => expect(calls.find((c: Call) => c.method === "POST")!.body).toEqual({ template_id: 3, key_date: "2026-10-30", for_user_id: 9, for_name: "" }));
});

test("a started checklist shows its steps and progress", async () => {
  const t = ticket();
  const child = (id: number, status: string, subject: string) => ({
    id, label: `#${id}`, subject, status, priority: "medium", queue: { id: 1, name: "Desk" }, assignee: null, created_at: "2026-10-20T00:00:00Z",
  });
  page({
    [`GET /api/tickets/${t.id}/checklists`]: [{
      id: 1, name: "Leaver", kind: "offboarding", for_name: "Pat Lee", for_user_id: 9, key_date: "2026-10-30", created_at: "2026-10-20T00:00:00Z",
      items: [
        { id: 1, title: "Collect laptop", offset_days: 0, due_on: "2026-10-30", action: null, run_id: null, child_ticket: child(2001, "resolved", "Collect laptop: Pat Lee") },
        { id: 2, title: "Disable account", offset_days: 1, due_on: "2026-10-31", action: "disable_ad_account", run_id: 7, child_ticket: child(2002, "new", "Disable account: Pat Lee") },
      ],
    }],
  });
  const list = await screen.findByRole("group", { name: "Leaver for Pat Lee" });
  expect(list).toHaveTextContent("1 of 2 done");
  expect(within(list).getByRole("link", { name: "Collect laptop" })).toHaveAttribute("href", "/tickets/2001");
  expect(list).toHaveTextContent("disables the AD account that day");
});

test("a checklist a rule started has no date yet: set it and the steps' dates follow", async () => {
  const t = ticket();
  const undated = { id: 4, name: "New starter", kind: "onboarding", for_name: "John Smith", for_user_id: null, key_date: null, created_at: "2026-10-20T00:00:00Z", items: [] };
  const { calls } = page({
    [`GET /api/tickets/${t.id}/checklists`]: [undated],
    [`PUT /api/tickets/${t.id}/checklists/4/date`]: { ...undated, key_date: "2026-11-09" },
  });
  const list = await screen.findByRole("group", { name: "New starter for John Smith" });
  await userEvent.type(within(list).getByLabelText(/No start date yet/), "2026-11-09");
  await userEvent.click(within(list).getByRole("button", { name: "Set" }));
  await waitFor(() => expect(calls.find((c: Call) => c.method === "PUT")!.body).toEqual({ key_date: "2026-11-09" }));
});
