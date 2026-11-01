// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet } from "react-router";

import { ME, mockApi, renderAt, ticket } from "../test/util";
import { CommandPalette } from "./CommandPalette";

afterEach(() => vi.unstubAllGlobals());

const PRINTER = ticket({ id: 4821, label: "#4821", subject: "Printer offline on 2nd floor", description: "Nothing prints since this morning.",
  requester: { id: 9, display_name: "R. Alvarez", email: "r@corp.local" } as never });
const TONER = ticket({ id: 4790, label: "#4790", subject: "Printer toner low", status: "resolved" });

function setup() {
  const calls = mockApi({
    "GET /api/tickets": { items: [PRINTER, TONER], total: 2 },
    "GET /api/tickets/4821": PRINTER,
    "GET /api/tickets/4790": TONER,
    "GET /api/tickets/4821/comments": [{ id: 1, body: "Restarted the spooler, still offline.", is_internal: true, author: { id: 5, display_name: "D. Cole" }, created_at: "2026-10-31T10:00:00Z" }],
    "GET /api/tickets/4790/comments": [],
    "GET /api/tickets/4821/problems": { problems: [{ id: 3, label: "PRB-00003", title: "Print server", status: "known_error", owner: null, ticket_count: 4, updated_at: "2026-10-31T10:00:00Z", workaround: "Use the 1st floor printer.", root_cause: "" }], hidden: 0 },
    "GET /api/tickets/4790/problems": { problems: [], hidden: 0 },
  });
  renderAt("/", [{ path: "/", element: <><CommandPalette me={ME} /><Outlet /></>, children: [
    { index: true, element: <h1>Queue</h1> }, { path: "tickets/:id", element: <h1>Ticket workspace</h1> },
  ] }]);
  return calls;
}

test("typing words lists matching tickets and previews the highlighted one", async () => {
  const calls = setup();
  await userEvent.keyboard("{Control>}k{/Control}");
  await userEvent.type(screen.getByRole("combobox"), "printer");
  const option = await screen.findByRole("option", { name: /#4821 Printer offline on 2nd floor/ });
  expect(calls.find((c) => c.path.startsWith("/api/tickets?"))?.path).toContain("scope=all");
  const steps = screen.getAllByRole("option").indexOf(option);
  if (steps > 0) await userEvent.keyboard("{ArrowDown}".repeat(steps));
  const preview = await screen.findByRole("complementary", { name: "Ticket preview" });
  expect(await within(preview).findByRole("heading", { name: "Printer offline on 2nd floor" })).toBeInTheDocument();
  expect(preview).toHaveTextContent("R. Alvarez");
  expect(await within(preview).findByText(/Restarted the spooler/)).toBeInTheDocument();
  expect(await within(preview).findByText(/Use the 1st floor printer/)).toBeInTheDocument();
  await userEvent.keyboard("{ArrowDown}");
  expect(await screen.findByRole("heading", { name: "Printer toner low" })).toBeInTheDocument();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "Ticket workspace" })).toBeInTheDocument();
});

test("a ticket number previews that ticket straight away", async () => {
  setup();
  await userEvent.keyboard("{Control>}k{/Control}");
  await userEvent.type(screen.getByRole("combobox"), "#4821");
  const preview = await screen.findByRole("complementary", { name: "Ticket preview" });
  expect(await within(preview).findByRole("heading", { name: "Printer offline on 2nd floor" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByRole("option")[0]).toHaveTextContent("Open ticket #4821"));
});
