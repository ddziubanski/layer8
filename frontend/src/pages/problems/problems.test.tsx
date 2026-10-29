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
