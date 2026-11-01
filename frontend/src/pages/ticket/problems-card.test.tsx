// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt, ticket } from "../../test/util";
import { ProblemsCard } from "./ProblemsCard";

afterEach(() => vi.unstubAllGlobals());

const VPN = { id: 3, label: "PRB-00003", title: "VPN drops after sleep", status: "known_error", owner: null, ticket_count: 4,
  updated_at: "2026-10-30T10:00:00Z", workaround: "Reconnect the client.", root_cause: "Driver power saving." };
const PRINT = { ...VPN, id: 5, label: "PRB-00005", title: "Printer jams", status: "investigating", workaround: "", root_cause: "" };

test("shows the linked problem's workaround, and someone else's hidden one only as a count", async () => {
  mockApi({ "GET /api/tickets/4821/problems": { problems: [VPN], hidden: 1 } });
  renderAt("/", [{ path: "/", element: <ProblemsCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  const card = await screen.findByRole("region", { name: "Problems" });
  expect(await within(card).findByRole("link", { name: "PRB-00003 VPN drops after sleep" })).toHaveAttribute("href", "/problems/3");
  expect(card).toHaveTextContent("Workaround: Reconnect the client.");
  expect(card).toHaveTextContent("Known error");
  expect(card).toHaveTextContent("Also part of 1 problem you can't open");
  expect(within(card).getByRole("link", { name: "Create problem from this ticket" })).toHaveAttribute("href", "/problems/new?ticket=4821");
});

test("finds a problem and links it; unlinks too", async () => {
  const calls = mockApi({
    "GET /api/tickets/4821/problems": { problems: [VPN], hidden: 0 },
    "GET /api/problems": [VPN, PRINT],
    "POST /api/tickets/4821/problems": { problems: [VPN, PRINT], hidden: 0 },
    "DELETE /api/tickets/4821/problems/3": { problems: [PRINT], hidden: 0 },
  });
  renderAt("/", [{ path: "/", element: <ProblemsCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  const card = await screen.findByRole("region", { name: "Problems" });
  await userEvent.click(await within(card).findByRole("button", { name: "Link to a problem…" }));
  // Already linked problems aren't offered again.
  const row = (await within(card).findByText("PRB-00005 Printer jams")).parentElement!;
  expect(within(card).queryByText("PRB-00003 VPN drops after sleep", { selector: "span" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Link" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ problem_id: 5 }));
  await userEvent.click(await within(card).findByRole("button", { name: "Unlink from PRB-00003" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/tickets/4821/problems/3")).toBe(true));
  expect(await within(card).findByRole("link", { name: "PRB-00005 Printer jams" })).toBeInTheDocument();
});

test("an open outage problem at the same site is offered as part of this outage", async () => {
  const calls = mockApi({
    "GET /api/tickets/4821/problems": { problems: [], hidden: 0, suggested: [{ problem: { ...PRINT, id: 12, label: "PRB-00012", title: "Branch outage", outage_scope: "Site Branch", workaround: "Use the hotspot." }, scope: "Part of this outage · Site Branch" }] },
    "POST /api/tickets/4821/problems": { problems: [], hidden: 0, suggested: [] },
  });
  renderAt("/", [{ path: "/", element: <ProblemsCard ticket={ticket()} onChanged={() => {}} /> }]);
  const offer = await screen.findByRole("group", { name: "Suggested: PRB-00012" });
  expect(offer).toHaveTextContent("Same Site Branch");
  expect(offer).toHaveTextContent("Workaround: Use the hotspot.");
  await userEvent.click(within(offer).getByRole("button", { name: "Link to PRB-00012" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ problem_id: 12 }));
});
