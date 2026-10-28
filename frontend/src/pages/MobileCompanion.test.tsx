// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MobileCompanion } from "./MobileCompanion";
import { ME, mockApi, renderAt, ticket } from "../test/util";

afterEach(() => vi.unstubAllGlobals());

test("searches all ticket statuses using the selected scope", async () => {
  const calls = mockApi({ "GET /api/tickets": { items: [ticket()], total: 1, limit: 25, offset: 0 } });
  renderAt("/mobile", [{ path: "/mobile", element: <MobileCompanion me={ME} /> }]);
  expect(await screen.findByText(/Switch port flapping/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "All tickets" }));
  await userEvent.type(screen.getByLabelText("Search tickets"), "4821");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(calls.some(c => c.path.includes("scope=all") && c.path.includes("q=4821") && !c.path.includes("view="))).toBe(true));
});

test("defaults to internal notes and explicitly sends requester email when selected", async () => {
  const calls = mockApi({ "GET /api/tickets/4821": ticket(), "GET /api/tickets/4821/comments": [], "POST /api/tickets/4821/comments": { id: 5 } });
  renderAt("/mobile/tickets/4821", [{ path: "/mobile/tickets/:ticketId", element: <MobileCompanion me={ME} /> }]);
  await screen.findByText(/Switch port flapping/);
  expect(screen.getByLabelText("Visibility")).toHaveValue("internal");
  await userEvent.selectOptions(screen.getByLabelText("Visibility"), "email");
  await userEvent.type(screen.getByLabelText("Message"), "We are investigating.");
  await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
  await waitFor(() => expect(calls.some(c => c.method === "POST" && (c.body as { is_internal?: boolean; email_requester?: boolean })?.is_internal === false && (c.body as { email_requester?: boolean })?.email_requester === true)).toBe(true));
  expect(screen.getByLabelText("Visibility")).toHaveValue("internal");
});
