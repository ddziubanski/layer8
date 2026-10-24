// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { QueuesAdmin } from "./QueuesAdmin";

const QUEUE = {
  id: 2,
  name: "Network",
  description: null,
  is_default: false,
  escalation_queue_id: null,
  is_active: true,
  show_in_portal: false,
  portal_name: null,
  portal_description: null,
  portal_position: 100,
  categories: [],
  category_routes: {},
  restricted: false,
  owner_users: [],
  owner_groups: [],
  manager_users: [],
  manager_groups: [],
  viewer_users: [],
  viewer_groups: [],
};
const USERS = [
  { ...ME, id: 5, display_name: "D. Cole", email: "dcole@corp.local" },
  { ...ME, id: 9, display_name: "R. Alvarez", email: "r@corp.local", role: "requester" },
];
const GROUPS = [{ id: 4, name: "SG-Network", description: null, source: "directory", directory_id: "x", is_active: true, members: [] }];

afterEach(() => vi.unstubAllGlobals());

test("admin edits portal settings, categories and owners", async () => {
  const calls = mockApi({
    "GET /api/queues": [QUEUE, { ...QUEUE, id: 1, name: "Service desk" }],
    "GET /api/users": USERS,
    "GET /api/groups": GROUPS,
    "PATCH /api/queues/2": QUEUE,
    "PUT /api/queues/2/owners": QUEUE,
    "PUT /api/queues/2/managers": QUEUE,
    "PUT /api/queues/2/viewers": QUEUE,
  });
  renderAt("/", [{ path: "/", element: <QueuesAdmin me={{ ...ME, role: "admin" }} /> }]);
  await userEvent.click(await screen.findByLabelText("Requesters can choose this queue"));
  await userEvent.click(screen.getByRole("button", { name: "Add option" }));
  await userEvent.type(screen.getByLabelText("Option 1"), "Wi-Fi");
  await userEvent.selectOptions(screen.getByLabelText("Send option 1 to"), "Send to Service desk");
  await userEvent.click(screen.getByRole("button", { name: "Add option" }));
  await userEvent.type(screen.getByLabelText("Option 2"), "VPN");
  // Requesters aren't offered as owners.
  expect(screen.queryByLabelText("R. Alvarez")).toBeNull();
  const [ownerGroup] = await screen.findAllByLabelText(/SG-Network/);
  await userEvent.click(ownerGroup);
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.path.endsWith("/managers"))).toBe(true));
  const patch = calls.find((c) => c.method === "PATCH")!.body as Record<string, unknown>;
  expect(patch.show_in_portal).toBe(true);
  expect(patch.categories).toEqual(["Wi-Fi", "VPN"]);
  expect(patch.category_routes).toEqual({ "Wi-Fi": 1 });
  expect(calls.find((c) => c.path.endsWith("/owners"))?.body).toEqual({ user_ids: [], group_ids: [4] });
});

test("an agent who doesn't manage the queue sees it read-only", async () => {
  mockApi({ "GET /api/queues": [QUEUE], "GET /api/users": USERS, "GET /api/groups": GROUPS });
  renderAt("/", [{ path: "/", element: <QueuesAdmin me={ME} /> }]);
  expect(await screen.findByText("Only admins and this queue's managers can change it.")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
});

test("admin restricts a queue and picks flat viewers", async () => {
  const calls = mockApi({
    "GET /api/queues": [QUEUE],
    "GET /api/users": USERS,
    "GET /api/groups": [{ id: 6, name: "HR auditors", description: null, source: "local", directory_id: null, is_active: true, members: [] }],
    "PATCH /api/queues/2": QUEUE,
    "PUT /api/queues/2/owners": QUEUE,
    "PUT /api/queues/2/managers": QUEUE,
    "PUT /api/queues/2/viewers": QUEUE,
  });
  renderAt("/", [{ path: "/", element: <QueuesAdmin me={{ ...ME, role: "admin" }} /> }]);
  expect(screen.queryByText(/Viewers:/)).toBeNull();
  await userEvent.click(await screen.findByLabelText(/Restricted: only this queue/));
  const viewers = screen.getByRole("group", { name: /Viewers:/ });
  await userEvent.click(within(viewers).getByLabelText(/HR auditors/));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(calls.find((c) => c.path.endsWith("/viewers"))?.body).toEqual({ user_ids: [], group_ids: [6] }));
  expect((calls.find((c) => c.method === "PATCH")!.body as Record<string, unknown>).restricted).toBe(true);
});

test("managers can pick viewers but not restrict", async () => {
  mockApi({ "GET /api/queues": [{ ...QUEUE, restricted: true, manager_users: [{ id: 1, email: ME.email, display_name: ME.display_name }] }], "GET /api/users": USERS, "GET /api/groups": [] });
  renderAt("/", [{ path: "/", element: <QueuesAdmin me={ME} /> }]);
  expect(await screen.findByLabelText(/Restricted: only this queue/)).toBeDisabled();
  expect(screen.getByText("Only admins can change this (or see Settings > Permissions: Queues).")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: /Viewers:/ })).not.toBeDisabled();
});
