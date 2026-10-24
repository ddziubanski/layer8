// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
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
    "PUT /api/layouts/ticket/mine": (body: unknown) => ({ page: "ticket", effective: body, default: USUAL, mine: body, required: ["conversation"] }),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={me} /> }]);
  return calls;
}

test("a saved layout hides and moves panels", async () => {
  page({ ...USUAL, main: ["history", ...USUAL.main.filter((p) => p !== "history" && p !== "expenses")], hidden: ["expenses"] });
  await screen.findByText("History");
  expect(screen.queryByRole("button", { name: "Add an expense" })).not.toBeInTheDocument();
});

test("arrange: hide a panel, move one up, save for me", async () => {
  const calls = page(USUAL);
  await userEvent.click(await screen.findByRole("button", { name: "Arrange this page" }));
  const bar = screen.getByRole("region", { name: "Arrange this page" });
  // The conversation can't be hidden.
  expect(screen.queryByRole("button", { name: "Hide Conversation" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Hide Expenses" }));
  expect(within(bar).getByRole("list", { name: "Hidden panels" })).toHaveTextContent("Expenses");
  await userEvent.click(screen.getByRole("button", { name: "Move History up" }));
  await userEvent.selectOptions(screen.getByLabelText("Where Status goes"), "main");
  // Only admins see "everyone's default".
  expect(within(bar).queryByRole("button", { name: "Save as everyone's default" })).not.toBeInTheDocument();
  await userEvent.click(within(bar).getByRole("button", { name: "Save for me" }));
  await waitFor(() => {
    const sent = calls.find((c: Call) => c.method === "PUT" && c.path === "/api/layouts/ticket/mine")!.body as typeof USUAL;
    expect(sent.hidden).toEqual(["expenses"]);
    expect(sent.main.slice(-3)).toEqual(["history", "scripts", "status"]);
    expect(sent.side).not.toContain("status");
  });
  expect(screen.queryByRole("region", { name: "Arrange this page" })).not.toBeInTheDocument();
});

test("arrange: a panel across the top, full width", async () => {
  const calls = page(USUAL);
  await userEvent.click(await screen.findByRole("button", { name: "Arrange this page" }));
  await userEvent.selectOptions(screen.getByLabelText("Where Attachments goes"), "top");
  expect(within(screen.getByRole("region", { name: "Across the top" })).getByRole("group", { name: "Panel: Attachments" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Where History goes"), "bottom");
  await userEvent.click(within(screen.getByRole("region", { name: "Arrange this page" })).getByRole("button", { name: "Save for me" }));
  await waitFor(() => {
    const sent = calls.find((c: Call) => c.method === "PUT" && c.path === "/api/layouts/ticket/mine")!.body as typeof USUAL;
    expect(sent.top).toEqual(["attachments"]);
    expect(sent.bottom).toEqual(["history"]);
    expect(sent.main).not.toContain("attachments");
  });
});

test("arrange: quick changes in a row all count", async () => {
  const calls = page(USUAL);
  await userEvent.click(await screen.findByRole("button", { name: "Arrange this page" }));
  const hideBoth = () => {
    screen.getByRole("button", { name: "Hide Expenses" }).click();
    screen.getByRole("button", { name: "Hide Systems" }).click();
  };
  hideBoth();
  await userEvent.click(within(screen.getByRole("region", { name: "Arrange this page" })).getByRole("button", { name: "Save for me" }));
  await waitFor(() => expect((calls.find((c: Call) => c.method === "PUT")!.body as typeof USUAL).hidden).toEqual(["expenses", "systems"]));
});
