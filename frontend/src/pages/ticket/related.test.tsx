// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { type Call, mockApi, renderAt, ticket } from "../../test/util";
import { RelatedTickets } from "./RelatedTickets";

const SUGGESTION = { ticket: { id: 4830, label: "#4830", subject: "2nd floor switch keeps dropping", status: "open", priority: "medium", queue: { id: 1, name: "Service desk" }, assignee: null, created_at: "2026-10-20T09:00:00Z" }, reasons: ["Subject shares: 2nd, floor, switch"] };

test("suggestions explain why; linking asks first and uses the parent link", async () => {
  const calls = mockApi({
    "GET /api/tickets/4821/related": [SUGGESTION],
    "PATCH /api/tickets/4830": ({ body }: Call) => ({ ...ticket({ id: 4830 }), ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <RelatedTickets ticket={ticket()} onChanged={() => {}} /> }]);
  expect(await screen.findByText(/Subject shares: 2nd, floor, switch/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Part of this ticket" }));
  expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  expect(screen.getByText(/Make #4830 part of this ticket\?/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Link" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ path: "/api/tickets/4830", body: { parent_id: 4821 } }));
});

test("nothing related says what would show", async () => {
  mockApi({ "GET /api/tickets/4821/related": [] });
  renderAt("/", [{ path: "/", element: <RelatedTickets ticket={ticket()} onChanged={() => {}} /> }]);
  expect(await screen.findByText(/None found/)).toBeInTheDocument();
});
