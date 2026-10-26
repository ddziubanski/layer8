// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderAt } from "../../test/util";
import { SharePointLibrary } from "./SharePointLibrary";

const status = { enabled: true, connected: true, site_url: "https://example.sharepoint.com/sites/IT" };
const reference = { title: "VPN FAQ", url: status.site_url + "/SitePages/VPN.aspx", drive_id: "drive-1", item_id: "item-1", summary: "Reviewed VPN instructions" };

test("Library sends only the typed search and linking requires review", async () => {
  const user = userEvent.setup();
  const calls = mockApi({ "GET /api/library/status": status, "POST /api/tickets/4821/library/search": [reference], "POST /api/tickets/4821/library/link": { comment_id: 12 } });
  renderAt("/", [{ path: "/", element: <SharePointLibrary ticketId={4821} /> }]);
  await screen.findByLabelText("Find a runbook or FAQ");
  expect(calls.every(c => c.method === "GET")).toBe(true);
  await user.type(screen.getByLabelText("Find a runbook or FAQ"), "VPN setup");
  await user.click(screen.getByRole("button", { name: "Search SharePoint" }));
  expect(await screen.findByRole("link", { name: "VPN FAQ" })).toHaveAttribute("href", reference.url);
  expect(calls.find(c => c.path.endsWith("/search"))?.body).toEqual({ query: "VPN setup" });
  await user.click(screen.getByRole("button", { name: "Link reference to ticket" }));
  const review = screen.getByRole("region", { name: "Review Library reference" });
  expect(review).toHaveFocus();
  expect(within(review).getByText(/internal note visible/)).toBeInTheDocument();
  expect(calls.some(c => c.path.endsWith("/link"))).toBe(false);
  await user.click(within(review).getByRole("button", { name: "Confirm internal reference" }));
  expect(await screen.findByText("Library reference saved as an internal note.")).toBeInTheDocument();
  expect(calls.find(c => c.path.endsWith("/link"))?.body).toEqual({ drive_id: "drive-1", item_id: "item-1", confirm: true });
});

test("a missing Microsoft grant offers reconnection without running a search", async () => {
  const calls = mockApi({ "GET /api/library/status": { ...status, connected: false } });
  renderAt("/", [{ path: "/", element: <SharePointLibrary ticketId={4821} /> }]);
  expect(await screen.findByRole("link", { name: "My preferences" })).toHaveAttribute("href", "/preferences");
  expect(screen.queryByRole("button", { name: "Search SharePoint" })).not.toBeInTheDocument();
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("provider failures are visible and no reference can be added", async () => {
  const user = userEvent.setup();
  const calls = mockApi({ "GET /api/library/status": status, "POST /api/tickets/4821/library/search": () => new Response(JSON.stringify({ detail: "SharePoint access was refused." }), { status: 409 }) });
  renderAt("/", [{ path: "/", element: <SharePointLibrary ticketId={4821} /> }]);
  await user.type(await screen.findByLabelText("Find a runbook or FAQ"), "VPN");
  await user.click(screen.getByRole("button", { name: "Search SharePoint" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("SharePoint access was refused.");
  expect(screen.queryByRole("button", { name: "Link reference to ticket" })).not.toBeInTheDocument();
  await waitFor(() => expect(calls.filter(c => c.method === "POST")).toHaveLength(1));
});
