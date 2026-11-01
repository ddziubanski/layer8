// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt, ticket } from "../../test/util";
import { ChecksCard } from "./ChecksCard";

afterEach(() => vi.unstubAllGlobals());

const CLEARED = { id: 4, kind: "reboot_done", label: "Restart pending clears on WS-0142", state: "cleared", detail: "No restart pending", device_id: 7, user_id: null,
  created_at: "2026-11-01T09:00:00Z", checked_at: "2026-11-01T10:00:00Z", cleared_at: "2026-11-01T10:00:00Z", confirmed_at: null, created_by: null, confirmed_by: null };

const OFFER = [{ kind: "updates_installed", device_id: 7, user_id: null, label: "Critical updates installed on WS-0142", now: "2 critical update(s) still missing" }];

test("offers what can be watched, and a cleared condition is verified here", async () => {
  let state = "cleared";
  const calls = mockApi({
    "GET /api/tickets/4821/checks": () => ({ checks: [{ ...CLEARED, state }], available: state === "cleared" ? OFFER : [] }),
    "UNUSED": { checks: [CLEARED], available: [{ kind: "updates_installed", device_id: 7, user_id: null, label: "Critical updates installed on WS-0142", now: "2 critical update(s) still missing" }] },
    "POST /api/tickets/4821/checks": { checks: [CLEARED], available: [] },
    "POST /api/tickets/4821/checks/4/confirm": () => { state = "confirmed"; return { checks: [{ ...CLEARED, state }], available: [] }; },
  });
  renderAt("/", [{ path: "/", element: <ChecksCard ticket={ticket()} onChanged={() => {}} /> }]);
  const cleared = await screen.findByRole("group", { name: "Restart pending clears on WS-0142" });
  expect(cleared).toHaveTextContent("Cleared: verify");
  await userEvent.click(screen.getByRole("button", { name: "Watch: Critical updates installed on WS-0142" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ kind: "updates_installed", device_id: 7, user_id: null }));
  await userEvent.click(within(cleared).getByRole("button", { name: "Verified, it's fixed" }));
  expect(await screen.findByText("Verified")).toBeInTheDocument();
});
