// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt } from "../test/util";
import { DeviceTimeline } from "./DeviceTimeline";

afterEach(() => vi.unstubAllGlobals());

const TIMELINE = {
  entries: [
    { at: "2026-10-30T14:00:00Z", kind: "change", title: "Change CHG-00012 completed", detail: "Replace dock firmware", tone: "ok", link: "/changes/12" },
    { at: "2026-10-29T09:30:00Z", kind: "script", title: 'Script "Clear print queue" failed', detail: "Version 3, exit code 1", tone: "bad", link: "/tickets/4821" },
    { at: "2026-10-29T09:00:00Z", kind: "ticket", title: "Ticket #4821 opened", detail: "Printer offline", tone: "info", link: "/tickets/4821" },
  ],
  now: [{ label: "Reboot", value: "pending", at: "2026-10-30T08:00:00Z", tone: "warn" }],
  upcoming: [{ at: "2027-01-15T12:00:00Z", kind: "lifecycle", title: "Warranty ends", detail: "", tone: "warn", link: null }],
  hidden: 2,
  truncated: false,
};

test("shows history newest first with links, right-now facts, what's coming and a hidden count", async () => {
  mockApi({ "GET /api/devices/5/timeline": TIMELINE });
  renderAt("/", [{ path: "/", element: <DeviceTimeline deviceId={5} /> }]);
  const card = await screen.findByRole("region", { name: "Timeline" });
  const items = await within(card).findAllByRole("listitem");
  expect(items[0]).toHaveTextContent("Change CHG-00012 completed");
  expect(within(card).getByRole("link", { name: "Change CHG-00012 completed" })).toHaveAttribute("href", "/changes/12");
  expect(items[1]).toHaveClass("tone-bad");
  expect(within(card).getByLabelText("Right now and coming up")).toHaveTextContent("Reboot pending");
  expect(within(card).getByLabelText("Right now and coming up")).toHaveTextContent("Coming up Warranty ends");
  expect(card).toHaveTextContent("2 more entries belong to tickets in queues you can't see.");
});

test("filters by kind", async () => {
  mockApi({ "GET /api/devices/5/timeline": TIMELINE });
  renderAt("/", [{ path: "/", element: <DeviceTimeline deviceId={5} /> }]);
  const card = await screen.findByRole("region", { name: "Timeline" });
  await userEvent.click(await within(card).findByRole("button", { name: "Scripts" }));
  expect(within(card).queryByText('Script "Clear print queue" failed')).toBeNull();
  expect(within(card).getAllByRole("listitem")).toHaveLength(2);
});
