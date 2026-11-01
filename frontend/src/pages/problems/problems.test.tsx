// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Problem } from "../../api/client";
import { ticket, ME, mockApi, renderAt, type Call } from "../../test/util";
import { ProblemsPage } from "./ProblemsPage";

afterEach(() => vi.unstubAllGlobals());

const PROBLEM: Problem = {
  id: 8,
  label: "PRB-00008",
  title: "Wi-Fi drops after resume",
  description: "Several laptops lose connection when waking.",
  status: "investigating",
  root_cause: "",
  workaround: "Reconnect to the network.",
  resolution: "",
  outage_scope: "",
  active_outage_devices: [],
  recovered_outage_devices: [],
  outage_recovered: false,
  suggested_tickets: [],
  notice: null,
  owner: { id: ME.id, display_name: ME.display_name, email: ME.email },
  created_by: { id: ME.id, display_name: ME.display_name, email: ME.email },
  created_at: "2026-10-28T10:00:00Z",
  updated_at: "2026-10-28T10:00:00Z",
  ticket_count: 0,
  tickets: [],
  updates: [{ id: 1, created_at: "2026-10-28T10:00:00Z", author: null, type: "created", body: "Problem created" }],
};

test("searchable problem list opens a record", async () => {
  mockApi({ "GET /api/problems": [{ ...PROBLEM, ticket_count: 2 }] });
  renderAt("/problems", [{ path: "/problems", element: <ProblemsPage /> }]);
  const row = await screen.findByRole("row", { name: /PRB-00008 Wi-Fi drops after resume/ });
  expect(within(row).getByRole("link")).toHaveAttribute("href", "/problems/8");
  expect(within(row).getByText("Investigating")).toBeInTheDocument();
  expect(within(row).getByText("2")).toBeInTheDocument();
});

test("create a problem, link a visible ticket and retain it on save", async () => {
  const calls = mockApi({
    "GET /api/users": [{ id: ME.id, display_name: ME.display_name, email: ME.email, role: "agent", is_active: true }],
    "GET /api/tickets/4821": ticket(),
    "POST /api/problems": ({ body }: Call) => ({ ...PROBLEM, ...(body as object), id: 9, label: "PRB-00009", ticket_count: 1, tickets: [{ id: 4821, label: "#4821", subject: "Switch port flapping", status: "open", created_at: "2026-10-28T10:00:00Z" }] }),
  });
  renderAt("/problems/new", [
    { path: "/problems/:problemId", element: <ProblemsPage /> },
  ]);
  await userEvent.type(await screen.findByLabelText("Title"), "Wi-Fi drops after resume");
  await userEvent.type(screen.getByLabelText("Description"), "Several laptops lose connection when waking.");
  await userEvent.type(screen.getByLabelText("Ticket number"), "4821");
  await userEvent.click(screen.getByRole("button", { name: "Link ticket" }));
  expect(await screen.findByRole("link", { name: /#4821 Switch port flapping/ })).toHaveAttribute("href", "/tickets/4821");
  await userEvent.click(screen.getByRole("button", { name: "Create problem" }));
  await waitFor(() => expect(calls.find((call) => call.method === "POST")!.body).toMatchObject({ title: "Wi-Fi drops after resume", ticket_ids: [4821] }));
  expect(await screen.findByRole("heading", { name: /PRB-00009 Wi-Fi drops after resume/ })).toBeInTheDocument();
});

test("problem updates are posted as internal investigation notes", async () => {
  let updates = [...PROBLEM.updates];
  mockApi({
    "GET /api/problems/8": () => ({ ...PROBLEM, updates }),
    "GET /api/users": [],
    "POST /api/problems/8/updates": ({ body }: Call) => {
      const update = { id: 2, created_at: "2026-10-28T10:10:00Z", author: { id: ME.id, display_name: ME.display_name, email: ME.email }, type: "note", body: (body as { body: string }).body };
      updates = [...updates, update];
      return update;
    },
  });
  renderAt("/problems/8", [{ path: "/problems/:problemId", element: <ProblemsPage /> }]);
  const form = await screen.findByRole("form", { name: "Problem details" });
  expect(await within(form).findByLabelText("Root cause")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Add an internal update"), "Reproduced on two laptops.");
  await userEvent.click(screen.getByRole("button", { name: "Add update" }));
  expect(await screen.findByText("Reproduced on two laptops.")).toBeInTheDocument();
  expect(screen.getByText("Internal to staff")).toBeInTheDocument();
});

const CLUSTER = {
  key: "site:branch", scope_kind: "site", scope_label: "Site Branch", devices: ["SW-BRANCH-1", "AP-BRANCH-1"], down_since: "2026-11-01T09:00:00Z",
  ticket_ids: [4821, 4822], suggested_title: "Outage at Site Branch (SW-BRANCH-1, AP-BRANCH-1)",
  suggested_notice_title: "Service outage: Site Branch", suggested_notice_message: "We are investigating a network outage affecting Site Branch.",
};

test("an outage without a problem is offered, and opening one links its tickets", async () => {
  const calls = mockApi({
    "GET /api/problems": [],
    "GET /api/problems/outage-clusters": [CLUSTER],
    "POST /api/problems/from-outage": { ...PROBLEM, id: 12, label: "PRB-00012", title: CLUSTER.suggested_title, outage_scope: "Site Branch" },
    "GET /api/problems/12": { ...PROBLEM, id: 12, label: "PRB-00012", title: CLUSTER.suggested_title, outage_scope: "Site Branch" },
    "GET /api/users": [],
  });
  renderAt("/problems", [{ path: "/problems", element: <ProblemsPage /> }, { path: "/problems/:problemId", element: <ProblemsPage /> }]);
  const offer = await screen.findByRole("group", { name: "Site Branch" });
  expect(offer).toHaveTextContent("2 devices down");
  await userEvent.click(within(offer).getByRole("button", { name: "Open a problem…" }));
  // Without the notices permission there's no draft notice to tick.
  expect(screen.queryByLabelText("Draft a service notice")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Open problem" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    title: CLUSTER.suggested_title, outage_scope: "Site Branch", ticket_ids: [4821, 4822], create_draft_notice: false,
  }));
  expect(await screen.findByRole("heading", { name: /PRB-00012 Outage at Site Branch/ })).toBeInTheDocument();
});

test("a recovered outage offers one update to everyone affected, sent from the problem", async () => {
  const recovered = {
    ...PROBLEM, outage_scope: "Site Branch", outage_recovered: true, recovered_outage_devices: ["SW-BRANCH-1"],
    tickets: [{ id: 4821, label: "#4821", subject: "Branch offline", status: "open", created_at: "2026-11-01T09:00:00Z" }],
    suggested_tickets: [{ ticket: { id: 4830, label: "#4830", subject: "Can't print at branch", status: "open", created_at: "2026-11-01T09:10:00Z" }, scope: "Part of this outage · Site Branch" }],
    notice: { id: 3, title: "Service outage: Site Branch", message: "", severity: "outage", audience: "everyone", starts_at: "2026-11-01T09:00:00Z", ends_at: null, enabled: true, state: "current", created_by: null, created_at: "2026-11-01T09:00:00Z" },
  };
  const calls = mockApi({
    "GET /api/problems/8": recovered,
    "GET /api/users": [],
    "POST /api/problems/8/broadcast/preview": { applied: false, fingerprint: "abc", emails: 1, skipped_closed: 0, resolve: false,
      tickets: [{ ticket: recovered.tickets[0], requester: "R. Lee", emailed: true, resolved: false, note: null, why_no_email: null }] },
  });
  renderAt("/problems/8", [{ path: "/problems/:problemId", element: <ProblemsPage /> }]);
  const outage = await screen.findByRole("region", { name: "Outage" });
  expect(outage).toHaveTextContent("Back up: SW-BRANCH-1");
  expect(outage).toHaveTextContent("Service notice: Service outage: Site Branch · Showing to users");
  // A ticket from the same site is offered; linking adds it to the problem being edited.
  const maybe = screen.getByRole("group", { name: "Part of this outage?" });
  await userEvent.click(within(maybe).getByRole("button", { name: "Link #4830" }));
  expect(screen.getByRole("link", { name: /#4830 Can't print at branch/ })).toBeInTheDocument();
  await userEvent.click(within(outage).getByRole("button", { name: "Update everyone affected…" }));
  await userEvent.type(screen.getByLabelText("Update for everyone affected"), "The branch is back online.");
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(await screen.findByRole("button", { name: "Send to 1 person" })).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/problems/8/broadcast/preview")).toBe(true);
});
