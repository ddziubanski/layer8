// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { type Call, ME, mockApi, renderAt, ticket } from "../test/util";
import { PreferencesPage } from "./me/PreferencesPage";
import { QueuePage } from "./QueuePage";

const COUNTS = { open: 30, unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 };
// 30 tickets, served in whatever slices the queue asks for.
const ALL = Array.from({ length: 30 }, (_, i) => ticket({ id: 5000 + i, subject: `Ticket ${i + 1}` }));
const serve = ({ path }: Call) => {
  const q = new URLSearchParams(path.split("?")[1]);
  const limit = Number(q.get("limit")), offset = Number(q.get("offset") ?? 0);
  return { items: ALL.slice(offset, offset + limit), total: ALL.length, limit, offset };
};

test("keep scrolling: the next batch loads at the end, each ticket once", async () => {
  const calls = mockApi({ "GET /api/tickets/views": COUNTS, "GET /api/tickets": serve });
  renderAt("/", [{ path: "/", element: <QueuePage me={{ ...ME, queue_list: { density: "comfortable", paging: "scroll", page_size: 25 } }} /> }]);
  expect(await screen.findByText("25 of 30 loaded")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("All 30 shown")).toBeInTheDocument();
  expect(screen.getAllByRole("row", { name: /Ticket \d+/ })).toHaveLength(30);
  expect(calls.some((c) => c.path.startsWith("/api/tickets?") && c.path.includes("offset=25") && c.path.includes("limit=25"))).toBe(true);
  expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
});

test("pages and compact rows when chosen", async () => {
  mockApi({ "GET /api/tickets/views": COUNTS, "GET /api/tickets": serve });
  renderAt("/", [{ path: "/", element: <QueuePage me={{ ...ME, queue_list: { density: "compact", paging: "pages", page_size: 25 } }} /> }]);
  expect(await screen.findByText("1–25 of 30")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Tickets" })).toHaveClass("compact");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("26–30 of 30")).toBeInTheDocument();
});

test("My preferences saves the ticket list choices", async () => {
  const calls = mockApi({
    "GET /api/queues": [],
    "GET /api/teams/connection": { configured: false, connected: false },
    "PUT /api/auth/me/preferences": ({ body }: Call) => ({ ...ME, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={ME} /> }]);
  const section = screen.getByRole("region", { name: "Ticket list" });
  await userEvent.selectOptions(within(section).getByLabelText("Density"), "compact");
  await userEvent.selectOptions(within(section).getByLabelText("Long lists"), "pages");
  await userEvent.selectOptions(within(section).getByLabelText("Tickets per page"), "50");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ queue_list: { density: "compact", paging: "pages", page_size: 50 } }));
});
