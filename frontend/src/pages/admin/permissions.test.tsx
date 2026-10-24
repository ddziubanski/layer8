// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { ActionsCard } from "../ticket/SidePanels";
import { PermissionsAdmin } from "./PermissionsAdmin";

afterEach(() => vi.unstubAllGlobals());

const RULES = [
  { key: "ticket_delete", area: "Tickets", label: "Delete tickets", help: "Gone for good.", default: "admins", who: "admins", group_ids: [] },
  { key: "rules_manage", area: "Settings areas", label: "Ticket rules: create, change, test and run", help: "", default: "admins", who: "admins", group_ids: [] },
];
const GROUPS = [
  { id: 3, name: "Service Desk Leads", description: null, source: "directory", directory_id: "g", is_active: true, members: [] },
  { id: 4, name: "Old team", description: null, source: "local", directory_id: null, is_active: false, members: [] },
];

test("hand ticket rules to a group, then save", async () => {
  const calls = mockApi({
    "GET /api/permissions": RULES,
    "GET /api/groups": GROUPS,
    "PUT /api/permissions": ({ body }: Call) => RULES.map((r) => ({ ...r, ...((body as Record<string, object>)[r.key] ?? {}) })),
  });
  renderAt("/", [{ path: "/", element: <PermissionsAdmin /> }]);
  const row = await screen.findByRole("group", { name: "Ticket rules: create, change, test and run" });
  expect(within(row).getByRole("option", { name: "Admins only (usual)" })).toBeInTheDocument();
  await userEvent.selectOptions(within(row).getByLabelText(/Who may/), "groups");
  // Inactive groups aren't offered.
  expect(within(row).queryByLabelText(/Old team/)).toBeNull();
  await userEvent.click(within(row).getByLabelText(/Service Desk Leads/));
  await userEvent.click(screen.getByRole("button", { name: "Save 1 change" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ rules_manage: { who: "groups", group_ids: [3] } }));
  expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
});

test("Delete ticket shows only with the permission, and asks first", async () => {
  const t = ticket();
  const calls = mockApi({ "GET /api/auth/me": { ...ME, permissions: [...(ME.permissions ?? []), "ticket_delete"] }, [`DELETE /api/tickets/${t.id}`]: null });
  vi.stubGlobal("confirm", () => true);
  renderAt("/t", [
    { path: "/t", element: <ActionsCard ticket={t} onChanged={() => {}} /> },
    { path: "/", element: <p>The queue</p> },
  ]);
  await userEvent.click(await screen.findByRole("button", { name: "Delete ticket…" }));
  expect(await screen.findByText("The queue")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(true);
});

test("no Delete or Merge without the permissions", async () => {
  mockApi({ "GET /api/auth/me": { ...ME, permissions: [] } });
  renderAt("/", [{ path: "/", element: <ActionsCard ticket={ticket()} onChanged={() => {}} /> }]);
  await waitFor(() => expect(screen.queryByRole("button", { name: /Merge into another ticket/ })).toBeNull());
  expect(screen.queryByRole("button", { name: "Delete ticket…" })).toBeNull();
});
