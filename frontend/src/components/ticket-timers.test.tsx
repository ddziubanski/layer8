// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../test/util";
import { ActiveTimers, TimerButton, elapsed } from "./TicketTimers";

afterEach(() => vi.unstubAllGlobals());

const RUNNING = { ticket_id: 4821, ticket_label: "#4821", subject: "Printer offline", user_id: ME.id, user_name: ME.display_name,
  started_at: new Date(Date.now() - 75_000).toISOString() };

test("elapsed reads as minutes and seconds, then hours", () => {
  const t0 = Date.parse("2026-11-01T10:00:00Z");
  expect(elapsed("2026-11-01T10:00:00Z", t0 + 42_000)).toBe("0:42");
  expect(elapsed("2026-11-01T10:00:00Z", t0 + 725_000)).toBe("12:05");
  expect(elapsed("2026-11-01T10:00:00Z", t0 + 3_725_000)).toBe("1:02:05");
});

test("the console bar lists my running timers, links them and stops them", async () => {
  let running = [RUNNING];
  const calls = mockApi({
    "GET /api/me/timers": () => running,
    "POST /api/tickets/4821/timer/stop": () => { running = []; return { id: 1, minutes: 2 }; },
  });
  renderAt("/", [{ path: "/", element: <ActiveTimers /> }]);
  const bar = await screen.findByRole("region", { name: "Active ticket timers" });
  expect(within(bar).getByRole("link", { name: /#4821 Printer offline/ })).toHaveAttribute("href", "/tickets/4821");
  expect(bar).toHaveTextContent(/1:1\d/);
  await userEvent.click(within(bar).getByRole("button", { name: "Stop the timer on #4821" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/tickets/4821/timer/stop")).toBe(true));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Active ticket timers" })).toBeNull());
});

test("the ticket's button starts my timer, then shows it running", async () => {
  let timers: typeof RUNNING[] = [];
  const calls = mockApi({
    "GET /api/tickets/4821/time": () => ({ total_minutes: 0, entries: [], timers }),
    "POST /api/tickets/4821/timer": () => { timers = [RUNNING]; return RUNNING; },
  });
  renderAt("/", [{ path: "/", element: <TimerButton ticketId={4821} myId={ME.id} closed={false} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Start timer" }));
  expect(await screen.findByRole("button", { name: /Stop timer 1:\d\d/ })).toBeInTheDocument();
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
});

test("a closed ticket offers no timer", async () => {
  mockApi({ "GET /api/tickets/4821/time": { total_minutes: 30, entries: [], timers: [] } });
  renderAt("/", [{ path: "/", element: <TimerButton ticketId={4821} myId={ME.id} closed /> }]);
  await waitFor(() => expect(screen.queryByRole("button")).toBeNull());
});
