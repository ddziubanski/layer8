// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import type { Person } from "../api/client";
import { ME, mockApi, renderAt, ticket } from "../test/util";
import { PersonPage } from "./PersonPage";
import { QueuePage } from "./QueuePage";

const COUNTS = { open: 1, unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 };
const PERSON: Person = {
  id: 7, display_name: "R. Alvarez", email: "ralvarez@corp.local", role: "requester", is_active: true, auth_source: "directory",
  title: "Accountant", department: "Finance", phone: "+1 555 0100", upn: "ralvarez@corp.local", sam_account_name: "ralvarez",
  in_directory: true, ou: "OU=Finance,DC=corp,DC=local", last_synced_at: null, presence: null, open_tickets: 1, all_tickets: 12,
  devices: [{ id: 3, name: "WS-0142", os: "Windows 11" }],
  mobile: "+1 555 0199", office: "Building 2", company: "Example Corp",
  manager: { id: 9, display_name: "M. Osei", title: "Finance manager" }, direct_reports: [{ id: 11, display_name: "K. Lee", title: null }],
};

test("the requester's name opens a contact card, not the ticket", async () => {
  const t = ticket({ requester: { id: 7, email: "ralvarez@corp.local", display_name: "R. Alvarez" } });
  mockApi({ "GET /api/tickets/views": COUNTS, "GET /api/tickets": { items: [t], total: 1, limit: 100, offset: 0 }, "GET /api/people/7": PERSON });
  renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }, { path: "/tickets/:id", element: <p>Ticket page</p> }]);
  await userEvent.click(await screen.findByRole("button", { name: "R. Alvarez" }));
  const card = await screen.findByRole("dialog", { name: "R. Alvarez: contact card" });
  expect(await within(card).findByText("Accountant · Finance")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "ralvarez@corp.local" })).toHaveAttribute("href", "mailto:ralvarez@corp.local");
  expect(within(card).getByRole("link", { name: "+1 555 0100" })).toHaveAttribute("href", "tel:+15550100");
  expect(within(card).getByText("1 open ticket · 12 tickets in all")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "View profile" })).toHaveAttribute("href", "/people/7");
  expect(screen.queryByText("Ticket page")).not.toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("the person page: contact, devices and their tickets", async () => {
  const calls = mockApi({
    "GET /api/people/7": PERSON,
    "GET /api/tickets": { items: [ticket({ id: 4821, subject: "Printer jam" })], total: 1, limit: 50, offset: 0 },
  });
  renderAt("/people/7", [{ path: "/people/:personId", element: <PersonPage /> }]);
  expect(await screen.findByRole("heading", { name: "R. Alvarez" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "WS-0142" })).toHaveAttribute("href", "/devices/3");
  expect(await screen.findByRole("link", { name: "Printer jam" })).toHaveAttribute("href", "/tickets/4821");
  expect(screen.getByText("OU=Finance,DC=corp,DC=local")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "M. Osei" })).toHaveAttribute("href", "/people/9");
  expect(screen.getByRole("link", { name: "+1 555 0199" })).toHaveAttribute("href", "tel:+15550199");
  expect(screen.getByText("Building 2")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Direct reports" })).getByRole("link", { name: "K. Lee" })).toHaveAttribute("href", "/people/11");
  expect(calls.some((c) => c.path.startsWith("/api/tickets?") && c.path.includes("requester_id=7") && c.path.includes("scope=all"))).toBe(true);
});
