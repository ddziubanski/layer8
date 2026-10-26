// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const USUAL = {
  top: [] as string[],
  bottom: [] as string[],
  main: ["conversation", "attachments", "children", "checklists", "expenses", "ad_account", "scripts", "history"],
  side: ["assignment", "requester", "computer", "devices", "systems", "changes", "status", "actions"],
  hidden: [],
};

function page(layout: Record<string, unknown>, me = ME) {
  const t = ticket();
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [],
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/scripts": [],
    "GET /api/runs": [],
    "GET /api/layouts/ticket": { page: "ticket", effective: layout, default: USUAL, mine: null, required: ["conversation"] },
    "PUT /api/layouts/ticket/mine": ({ body }: Call) => ({ page: "ticket", effective: body, default: USUAL, mine: body, required: ["conversation"] }),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={me} /> }]);
  return calls;
}

test("legacy layouts use Ticket Focus and cannot hide tools or restore the widget canvas", async () => {
  const calls = page({ ...USUAL, hidden: ["expenses", "scripts"] });
  const draft = await screen.findByLabelText("Work notes and replies");
  await userEvent.type(draft, "Keep this reply");
  expect(screen.queryByRole("button", { name: "Arrange this page" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Tools" }));
  expect(within(screen.getByRole("group", { name: "Ticket tools" })).getByRole("button", { name: /Scripts/ })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Conversation" }));
  expect(screen.getByLabelText("Work notes and replies")).toBe(draft);
  expect(draft).toHaveValue("Keep this reply");
  expect(calls.some(call => call.method === "PUT" && call.path.startsWith("/api/layouts"))).toBe(false);
});
