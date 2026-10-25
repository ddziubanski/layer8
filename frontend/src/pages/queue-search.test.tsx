// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { type Call, ME, mockApi, renderAt, ticket } from "../test/util";
import { QueuePage } from "./QueuePage";

const COUNTS = { open: 1, unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 };

test("searching covers every status, newest first; a view or Clear ends it", async () => {
  const calls = mockApi({
    "GET /api/tickets/views": COUNTS,
    "GET /api/tickets": ({ path }: Call) => path.includes("q=")
      ? { items: [ticket({ id: 1200, subject: "Old toner ticket", status: "closed" })], total: 1, limit: 100, offset: 0 }
      : { items: [ticket()], total: 1, limit: 100, offset: 0 },
  });
  renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);
  await screen.findByRole("row", { name: /Switch port flapping/ });
  await userEvent.type(screen.getByRole("searchbox", { name: "Search tickets" }), "toner");
  expect(await screen.findByRole("row", { name: /Old toner ticket/ })).toBeInTheDocument();
  expect(screen.getByText(/1 ticket match “toner”, any status/)).toBeInTheDocument();
  const searched = calls.filter((c) => c.path.startsWith("/api/tickets?") && c.path.includes("q=toner")).at(-1)!.path;
  expect(searched).toContain("sort=age");
  expect(searched).toContain("dir=asc");
  expect(searched).not.toContain("view=");
  expect(screen.getByRole("button", { name: /All open/ })).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(screen.getByRole("button", { name: "Clear search" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /All open/ })).toHaveAttribute("aria-pressed", "true"));
  expect(await screen.findByRole("row", { name: /Switch port flapping/ })).toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Search tickets" })).toHaveValue("");
});
