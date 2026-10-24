// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt } from "../test/util";
import { Composer } from "./ticket/Composer";
import { TimeCard } from "./ticket/TimeCard";

afterEach(() => vi.unstubAllGlobals());

test("the Time panel shows the total and logs more", async () => {
  const calls = mockApi({
    "GET /api/tickets/4821/time": { total_minutes: 75, entries: [{ id: 1, minutes: 75, worked_on: "2026-10-23", note: "Reimage", user_id: 2, user_name: "J. Park", comment_id: null, created_at: new Date().toISOString(), may_delete: true }] },
    "POST /api/tickets/4821/time": { id: 2 },
  });
  renderAt("/", [{ path: "/", element: <TimeCard ticketId={4821} /> }]);
  expect(await screen.findByText("Time spent: 1 h 15 min")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Minutes"), "20");
  await userEvent.click(screen.getByRole("button", { name: "Log time" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ minutes: 20, note: "" }));
});

test("time typed in the composer is logged with the note", async () => {
  const calls = mockApi({
    "GET /api/canned-replies": [],
    "POST /api/tickets/4821/comments": { id: 77 },
    "POST /api/tickets/4821/time": { id: 3 },
  });
  renderAt("/", [{ path: "/", element: <Composer ticketId={4821} onDone={() => undefined} /> }]);
  await userEvent.type(screen.getByLabelText("Work notes and replies"), "Swapped the dock.");
  await userEvent.type(screen.getByLabelText("Time spent, minutes"), "25");
  await userEvent.click(screen.getByRole("button", { name: "Add internal note" }));
  await waitFor(() => expect(calls.find((c) => c.path === "/api/tickets/4821/time")?.body).toEqual({ minutes: 25, note: "", comment_id: 77 }));
});
