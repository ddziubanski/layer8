// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { QueuesAdmin } from "./QueuesAdmin";

const BASE = {
  description: null, is_default: false, escalation_queue_id: null, is_active: true, archived_at: null, show_in_portal: false,
  portal_name: null, portal_description: null, portal_position: 100, categories: [], category_routes: {}, restricted: false, hours: null,
  email_addresses: [], owner_users: [], owner_groups: [], manager_users: [], manager_groups: [], viewer_users: [], viewer_groups: [],
};
const ADMIN = { ...ME, role: "admin" as const };

function setup(old: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  const calls = mockApi({
    "GET /api/queues": [{ ...BASE, id: 2, name: "Old projects", ...old }, { ...BASE, id: 1, name: "Service desk", is_default: true }],
    "GET /api/users": [], "GET /api/groups": [],
    ...extra,
  });
  renderAt("/", [{ path: "/", element: <QueuesAdmin me={ADMIN} /> }]);
  return calls;
}

test("Delete needs the queue marked inactive first", async () => {
  setup({});
  const retire = await screen.findByRole("group", { name: "Retire this queue" });
  expect(within(retire).getByRole("button", { name: /Delete queue/ })).toBeDisabled();
  expect(retire).toHaveTextContent("Untick Active and save to enable Delete.");
});

test("an inactive queue's tickets move on delete, confirmed by typing its name", async () => {
  const calls = setup({ is_active: false }, {
    "GET /api/queues/2/delete-check": { tickets: 3, open_tickets: 1, blockers: [] },
    "DELETE /api/queues/2": new Response(null, { status: 204 }),
  });
  const retire = await screen.findByRole("group", { name: "Retire this queue" });
  await userEvent.click(within(retire).getByRole("button", { name: /Delete queue/ }));
  const dialog = await screen.findByRole("dialog", { name: "Delete Old projects?" });
  const go = within(dialog).getByRole("button", { name: "Delete permanently" });
  await userEvent.selectOptions(await within(dialog).findByLabelText(/Move its 3 tickets/), "Service desk");
  expect(go).toBeDisabled();
  await userEvent.type(within(dialog).getByLabelText(/to confirm/), "Old projects");
  await userEvent.click(go);
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/api/queues/2?move_to=1");
});

test("blockers are listed and stop the delete", async () => {
  setup({ is_active: false }, { "GET /api/queues/2/delete-check": { tickets: 0, open_tickets: 0, blockers: ["Routing rule Projects mail uses it"] } });
  await userEvent.click(within(await screen.findByRole("group", { name: "Retire this queue" })).getByRole("button", { name: /Delete queue/ }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Routing rule Projects mail uses it")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Delete permanently" })).toBeDisabled();
});

test("archive asks first; archived queues hide behind Show archived", async () => {
  const calls = setup({}, { "POST /api/queues/2/archive": { ...BASE, id: 2, name: "Old projects", is_active: false, archived_at: "2026-10-27T10:00:00Z" } });
  await userEvent.click(within(await screen.findByRole("group", { name: "Retire this queue" })).getByRole("button", { name: "Archive…" }));
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Archive queue" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ archived: true }));
});

test("archived queues are hidden until Show archived is ticked", async () => {
  setup({ is_active: false, archived_at: "2026-10-27T10:00:00Z" });
  await screen.findByRole("heading", { name: "Service desk" });
  expect(screen.queryByRole("button", { name: /Old projects/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Show archived (1)"));
  expect(screen.getByRole("button", { name: /Old projects/ })).toHaveTextContent("archived");
});
