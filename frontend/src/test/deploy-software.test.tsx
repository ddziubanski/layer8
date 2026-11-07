// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeploySoftware } from "../pages/devices/DeploySoftware";
import { ME, mockApi, renderAt } from "./util";

afterEach(() => vi.unstubAllGlobals());
const ORG = "org-1";
const review = {
  id: "b-1", ticket_id: null, state: "review", message: "Review these machines, packages and time. Nothing has been sent to Action1.",
  created_at: "2026-11-06T10:00:00Z", submitted_at: null, instance_id: null, schedule_id: null, organization: "Example Org", platform: "Windows",
  targets: [{ type: "Endpoint", id: "e1", name: "PC-001" }, { type: "Endpoint", id: "e2", name: "PC-002" }],
  members: [{ name: "PC-001", connection: "Connected" }, { name: "PC-002", connection: "Disconnected" }], member_count: 2,
  packages: [{ package_id: "Viewer", package_name: "Example Viewer", version: "1.0", version_id: "Viewer-v1" }], run_at: null, retry_minutes: "30", results: [],
};

function setup(role: "admin" | "agent" = "admin") {
  const calls = mockApi({
    "GET /api/action1/deploy/organizations": [{ id: ORG, name: "Example Org" }],
    [`GET /api/action1/deploy/organizations/${ORG}/machines`]: [{ device_id: 1, name: "PC-001", os: "Windows 11", last_seen: null }, { device_id: 2, name: "PC-002", os: "Windows 11", last_seen: null }],
    [`GET /api/action1/deploy/organizations/${ORG}/groups`]: [{ id: "g1", name: "Front desk", description: "" }],
    [`GET /api/action1/deploy/organizations/${ORG}/software`]: { items: [{ id: "Viewer", name: "Example Viewer", vendor: "Example", platform: "Windows", versions: [{ id: "Viewer-v1", version: "1.0" }] }], more: false, offset: 0 },
    "GET /api/action1/deploy/bundles": [],
    "GET /api/action1/batches": [{ ...review, id: "old", state: "warning", message: "Action1 reports warnings.", results: [{ endpoint: "PC-009", status: "Error", time: "2026-11-05T10:00:00Z", details: "Exit code 1603" }] }],
    "POST /api/action1/batches/preview": review,
    "POST /api/action1/batches/b-1/submit": { ...review, state: "pending", instance_id: "i-1", message: "Accepted by Action1; installation is not yet confirmed." },
  });
  renderAt("/", [{ path: "/", element: <DeploySoftware me={{ ...ME, role }} /> }]);
  return calls;
}

test("choose machines and a package, review (nothing sent), then deploy once", async () => {
  const calls = setup(); const user = userEvent.setup();
  const form = await screen.findByRole("region", { name: "Deploy software through Action1" });
  await user.click(await within(form).findByRole("checkbox", { name: /PC-001/ }));
  await user.click(within(form).getByRole("checkbox", { name: /PC-002/ }));
  await user.type(within(form).getByLabelText("Search software"), "viewer");
  await user.click(within(form).getByRole("button", { name: "Search" }));
  await user.click(await within(form).findByRole("button", { name: "Add" }));
  expect(within(form).getByRole("list", { name: "Chosen packages" })).toHaveTextContent("Example Viewer 1.0");
  await user.click(within(form).getByRole("button", { name: "Review" }));
  const card = await screen.findByRole("region", { name: "Deployment review" });
  expect(card).toHaveTextContent("Example Viewer 1.0 to PC-001, PC-002");
  const sent = calls.find(c => c.path.endsWith("/batches/preview"))!.body as Record<string, unknown>;
  expect(sent).toMatchObject({ organization_id: ORG, device_ids: [1, 2], group_id: null, packages: [{ package_id: "Viewer", version_id: "Viewer-v1" }], run_at: null, retry_minutes: "30" });
  expect(calls.some(c => c.path.endsWith("/submit"))).toBe(false);
  const deploy = within(card).getByRole("button", { name: "Deploy" });
  expect(deploy).toBeDisabled();
  await user.click(within(card).getByRole("checkbox", { name: /I've reviewed/ }));
  await user.click(deploy);
  const recent = screen.getByRole("region", { name: "Recent deployments" });
  expect(await within(recent).findByText(/Accepted by Action1/)).toBeInTheDocument();
  expect(calls.filter(c => c.path.endsWith("/submit"))).toHaveLength(1);
  // A changed choice drops the review.
  await user.click(within(form).getByRole("checkbox", { name: /PC-001/ }));
  expect(screen.queryByRole("region", { name: "Deployment review" })).not.toBeInTheDocument();
});

test("a group, scheduled once, with per-machine results in the history", async () => {
  const calls = setup(); const user = userEvent.setup();
  const form = await screen.findByRole("region", { name: "Deploy software through Action1" });
  await user.click(await within(form).findByRole("radio", { name: "An Action1 group" }));
  await user.selectOptions(await within(form).findByLabelText("Action1 group"), "g1");
  await user.type(within(form).getByLabelText("Search software"), "viewer{Enter}");
  await user.click(await within(form).findByRole("button", { name: "Add" }));
  await user.click(within(form).getByRole("radio", { name: "Once, at" }));
  await user.type(within(form).getByLabelText("Date and time"), "2030-01-15T07:30");
  await user.selectOptions(within(form).getByLabelText(/Keep trying offline machines/), "1440");
  await user.click(within(form).getByRole("button", { name: "Review" }));
  await screen.findByRole("region", { name: "Deployment review" });
  const sent = calls.find(c => c.path.endsWith("/batches/preview"))!.body as Record<string, unknown>;
  expect(sent).toMatchObject({ device_ids: [], group_id: "g1", retry_minutes: "1440" });
  expect(new Date(String(sent.run_at)).getTime()).toBe(new Date("2030-01-15T07:30").getTime());
  const recent = screen.getByRole("region", { name: "Recent deployments" });
  await user.click(within(recent).getByText(/Per machine/));
  expect(within(recent).getByText("Exit code 1603")).toBeInTheDocument();
});

test("only admins can save bundles", async () => {
  setup("agent"); const user = userEvent.setup();
  const form = await screen.findByRole("region", { name: "Deploy software through Action1" });
  await user.type(await within(form).findByLabelText("Search software"), "viewer{Enter}");
  await user.click(await within(form).findByRole("button", { name: "Add" }));
  expect(within(form).queryByLabelText("Bundle name")).not.toBeInTheDocument();
});
