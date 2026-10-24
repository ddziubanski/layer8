// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { DirectoryAdmin } from "./DirectoryAdmin";
import { UsersAdmin } from "./UsersAdmin";

const ADMIN = { ...ME, role: "admin" as const };
const STATUS = { configured: true, kind: "ad", servers: ["ldaps://dc1.corp.local"], base_dn: "DC=corp,DC=local", sync_minutes: 30, last_run: null, tracked_groups: 1, directory_users: 40 };
const ADMINS_MAPPING = { group: { id: 3, name: "SG-Helpdesk-Admins", source: "directory" }, role: "admin", member_count: 2, last_synced_at: null, sync_error: null };

afterEach(() => vi.unstubAllGlobals());

function setup(previewError: string | null = null) {
  return mockApi({
    "GET /api/directory/status": STATUS,
    "GET /api/directory/runs": [],
    "GET /api/settings/directory": {
      section: "directory",
      fields: [
        { name: "ldap_url", label: "Domain controllers", help: "", kind: "text", value: "ldaps://dc1.corp.local", locked: false, choices: [], is_set: null },
        { name: "ldap_kind", label: "Directory type", help: "", kind: "choice", value: "ad", locked: false, choices: [{ value: "ad", label: "Active Directory" }, { value: "generic", label: "Other LDAP" }], is_set: null },
        { name: "ldap_bind_password", label: "Service account password", help: "", kind: "secret", value: null, locked: false, choices: [], is_set: true },
      ],
    },
    "GET /api/directory/role-mappings": [ADMINS_MAPPING],
    "GET /api/directory/groups/search": [{ guid: "g-agents", dn: "CN=SG-Helpdesk-Agents,OU=Groups,DC=corp,DC=local", name: "SG-Helpdesk-Agents", description: null, group_id: null }],
    "POST /api/directory/groups/import": { id: 4, name: "SG-Helpdesk-Agents", description: null, source: "directory", directory_id: "g-agents", is_active: true, members: [{ id: 9, email: "a@corp.local", display_name: "A" }] },
    "POST /api/directory/role-mappings/preview": { error: previewError, changes: previewError ? [] : [{ kind: "role", user: "jpark@corp.local", text: "requester → agent (via SG-Helpdesk-Agents)" }], admins: 2, agents: 1 },
    "PUT /api/directory/role-mappings": [ADMINS_MAPPING, { ...ADMINS_MAPPING, group: { id: 4, name: "SG-Helpdesk-Agents", source: "directory" }, role: "agent", member_count: 1 }],
    "POST /api/directory/lookup": { found: true, guid: "u1", dn: "CN=M. Osei", email: "mosei@corp.local", display_name: "M. Osei", enabled: true, upn: "mosei@corp.local", sam: "mosei", aliases: [], groups: ["SG-Helpdesk-Admins"], role: "admin", role_source: "group:SG-Helpdesk-Admins" },
  });
}

test("adding an AD group previews who changes, then saves", async () => {
  const calls = setup();
  renderAt("/", [{ path: "/", element: <DirectoryAdmin me={ADMIN} /> }]);
  expect(await screen.findByText("SG-Helpdesk-Admins")).toBeInTheDocument();
  expect(screen.getByText("2 members (including nested groups)")).toBeInTheDocument();

  await userEvent.type(screen.getByLabelText("Add an AD group to agents"), "helpdesk");
  await userEvent.click(await screen.findByRole("option", { name: /SG-Helpdesk-Agents/ }));
  expect(await screen.findByText("requester → agent (via SG-Helpdesk-Agents)")).toBeInTheDocument();
  expect(screen.getByText(/2 admin\(s\) and 1 agent\(s\) afterwards/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Save permissions" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual([{ group_id: 3, role: "admin" }, { group_id: 4, role: "agent" }]));
});

test("a mapping that would leave no admin can't be saved", async () => {
  setup("These role mappings would leave no active admin; roles were not changed.");
  renderAt("/", [{ path: "/", element: <DirectoryAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Remove SG-Helpdesk-Admins from Admins" }));
  expect(await screen.findByText(/would leave no active admin/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save permissions" })).toBeDisabled();
});

test("check a person shows their role and where it comes from", async () => {
  setup();
  renderAt("/", [{ path: "/", element: <DirectoryAdmin me={ADMIN} /> }]);
  await userEvent.type(await screen.findByLabelText("Email, UPN or username"), "mosei");
  await userEvent.click(screen.getByRole("button", { name: "Look up" }));
  expect(await screen.findByText("(via SG-Helpdesk-Admins)")).toBeInTheDocument();
});

test("users: AD accounts are read-only, local ones editable", async () => {
  const calls = mockApi({
    "GET /api/users": [
      { ...ME, id: 2, display_name: "J. Park", email: "jpark@corp.local", auth_source: "directory", role_source: "group:SG-Helpdesk-Agents" },
      { ...ME, id: 3, display_name: "Break Glass", email: "admin@corp.local", role: "admin", auth_source: "local", role_source: "manual" },
    ],
    "PATCH /api/users/3": {},
  });
  renderAt("/", [{ path: "/", element: <UsersAdmin me={ADMIN} /> }]);
  const park = (await screen.findByText("J. Park")).closest("tr")!;
  expect(within(park).getByText("via SG-Helpdesk-Agents")).toBeInTheDocument();
  // The role comes from AD: no role picker, only the sign-in method.
  expect(within(park).queryByLabelText("Role for J. Park")).toBeNull();
  expect(within(park).getByLabelText("Sign-in for J. Park")).toHaveValue("directory");
  const glass = screen.getByText("Break Glass").closest("tr")!;
  await userEvent.selectOptions(within(glass).getByLabelText("Role for Break Glass"), "agent");
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "agent" }));
});

test("users: an AD technician can get a local fallback for when AD is down", async () => {
  const calls = mockApi({
    "GET /api/users": [{ ...ME, id: 2, display_name: "J. Park", email: "jpark@corp.local", auth_source: "directory", role_source: "group:SG-Helpdesk-Agents" }],
    "PUT /api/users/2/sign-in": {},
  });
  renderAt("/", [{ path: "/", element: <UsersAdmin me={ADMIN} /> }]);
  await userEvent.selectOptions(await screen.findByLabelText("Sign-in for J. Park"), "AD + local fallback");
  expect(screen.getByText("Signs in with AD. The local password only works while AD can't be reached.")).toBeInTheDocument();
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Fallback password for J. Park"), "fallback-pass-1234");
  await userEvent.click(save);
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ mode: "directory_with_fallback", password: "fallback-pass-1234" }));
});

test("groups: local groups are edited here, AD groups are read-only", async () => {
  const { GroupsAdmin } = await import("./GroupsAdmin");
  const calls = mockApi({
    "GET /api/groups": [
      { id: 6, name: "HR auditors", description: null, source: "local", directory_id: null, is_active: true, members: [] },
      { id: 3, name: "SG-Helpdesk-Admins", description: null, source: "directory", directory_id: "g", is_active: true, members: [{ id: 2, email: "m@corp.local", display_name: "M. Osei" }] },
    ],
    "GET /api/users": [{ ...ME, id: 2, display_name: "M. Osei", email: "m@corp.local" }, { ...ME, id: 4, display_name: "Pat HR", email: "pat@corp.local" }],
    "PUT /api/groups/6/members": {},
  });
  renderAt("/", [{ path: "/", element: <GroupsAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByLabelText(/Pat HR/));
  await userEvent.click(screen.getByRole("button", { name: "Save members" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ user_ids: [4] }));
  await userEvent.click(screen.getByRole("button", { name: /SG-Helpdesk-Admins/ }));
  expect(await screen.findByText(/Members come from Active Directory/)).toBeInTheDocument();
  expect(screen.getByLabelText(/M\. Osei/)).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save members" })).toBeNull();
});

test("connection settings sit under the directory status; the password is only ever replaced", async () => {
  const calls = setup();
  renderAt("/", [{ path: "/", element: <DirectoryAdmin me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Connection settings" });
  expect(within(form).getByLabelText("Directory type")).toHaveValue("ad");
  const password = within(form).getByLabelText("Service account password");
  expect(password).toHaveValue("");
  expect(password).toHaveAttribute("placeholder", expect.stringMatching(/^Set/));
  await userEvent.type(password, "New-bind-pass-1");
  await userEvent.click(within(form).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PUT" && c.path === "/api/settings/directory")?.body).toEqual({ values: { ldap_bind_password: "New-bind-pass-1" }, clear: [] }),
  );
});
