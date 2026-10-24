// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, type Call } from "../test/util";
import { SoftwareDeployment } from "./SoftwareDeployment";

afterEach(() => vi.unstubAllGlobals());
const me = { ...ME, role: "admin" as const };
const reviewed = { id: "00000000-0000-0000-0000-000000000042", device_id: 5, ticket_id: 4821,
  state: "review", message: "Nothing has been submitted.", instance_id: null, created_at: new Date().toISOString(),
  details: { package_name: "Example Viewer", package_id: "viewer", version: "1.2.3", version_id: "v1", vendor: "Example",
    device_name: "PC-0042", endpoint_name: "PC-0042", endpoint_id: "endpoint-1", organization: "Example Org", organization_id: "org-1",
    platform: "Windows", connection: "Connected", installed: [] } };

function setup(over: Record<string, unknown> = {}) {
  const calls = mockApi({
    "GET /api/action1/devices/5/deployments": [],
    "GET /api/action1/devices/5/software": { items: [{ id: "viewer", name: "Example Viewer", vendor: "Example", platform: "Windows", versions: [{ id: "v1", version: "1.2.3" }] }], more: false, offset: 0 },
    "POST /api/action1/deployments/preview": reviewed,
    [`POST /api/action1/deployments/${reviewed.id}/submit`]: { ...reviewed, state: "pending", instance_id: "instance-1", message: "Accepted by Action1; installation is not yet confirmed." },
    ...over,
  });
  renderAt("/", [{ path: "/", element: <SoftwareDeployment me={me} deviceId={5} ticketId={4821} /> }]);
  return calls;
}

async function choose() {
  await userEvent.click(screen.getByRole("button", { name: /Install software through Action1/ }));
  await userEvent.type(screen.getByLabelText("Search Action1 software"), "viewer");
  await userEvent.click(screen.getByRole("button", { name: "Search catalogue" }));
  await userEvent.selectOptions(await screen.findByLabelText("Application"), "viewer");
  await userEvent.selectOptions(screen.getByLabelText("Version"), "v1");
  await userEvent.click(screen.getByRole("button", { name: "Review deployment" }));
  await screen.findByRole("button", { name: "Deploy reviewed software" });
}

test("search and preview do not deploy; only the explicit reviewed submit starts it", async () => {
  const calls = setup();
  await choose();
  expect(calls.some(c => c.path.endsWith("/submit"))).toBe(false);
  expect(screen.getByText(/Automatic reboot disabled/)).toBeInTheDocument();
  expect(screen.getByText(/this does not prove it is absent/)).toBeInTheDocument();
  expect(calls.find(c => c.path.endsWith("/preview"))?.body).toEqual({ device_id: 5, ticket_id: 4821, package_id: "viewer", version_id: "v1" });
  await userEvent.click(screen.getByRole("button", { name: "Deploy reviewed software" }));
  expect(await screen.findByText(/installation is not yet confirmed/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deploy reviewed software" })).not.toBeInTheDocument();
  expect(calls.filter(c => c.path.endsWith("/submit"))).toHaveLength(1);
});

test("changing search or package clears the old choices", async () => {
  const calls = setup();
  await choose();
  await userEvent.click(screen.getByRole("button", { name: "Back to selection" }));
  await userEvent.clear(screen.getByLabelText("Search Action1 software"));
  expect(screen.queryByLabelText("Application")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deploy reviewed software" })).not.toBeInTheDocument();
  expect(calls.some(c => c.path.endsWith("/submit"))).toBe(false);
});

test("an uncertain outcome has no resend button", async () => {
  setup({ [`POST /api/action1/deployments/${reviewed.id}/submit`]: { ...reviewed, state: "unknown", message: "Do not resend. Check Action1." } });
  await choose();
  await userEvent.click(screen.getByRole("button", { name: "Deploy reviewed software" }));
  expect(await screen.findByText("Do not resend. Check Action1.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deploy reviewed software" })).not.toBeInTheDocument();
});

test("submission controls stay locked while pending", async () => {
  let release!: (r: Response) => void;
  const calls: Call[] = [];
  setup();
  await choose();
  const original = globalThis.fetch;
  vi.stubGlobal("fetch", (input: string, init: RequestInit) => {
    if (String(input).endsWith("/submit")) {
      calls.push({ method: "POST", path: input, body: null });
      return new Promise<Response>(resolve => { release = resolve; });
    }
    return original(input, init);
  });
  await userEvent.click(screen.getByRole("button", { name: "Deploy reviewed software" }));
  expect(screen.getByRole("button", { name: "Submitting…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Back to selection" })).toBeDisabled();
  release(new Response(JSON.stringify({ ...reviewed, state: "pending" }), { status: 200 }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Submitting…" })).not.toBeInTheDocument());
  expect(calls).toHaveLength(1);
});

test("permission denied and disabled configurations are visible without deployment", async () => {
  const calls = setup({ "GET /api/action1/devices/5/software": new Response(JSON.stringify({ detail: "Software deployment is off" }), { status: 409 }) });
  await userEvent.click(screen.getByRole("button", { name: /Install software/ }));
  await userEvent.type(screen.getByLabelText("Search Action1 software"), "viewer");
  await userEvent.click(screen.getByRole("button", { name: "Search catalogue" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Software deployment is off");
  expect(calls.some(c => c.path.endsWith("/submit"))).toBe(false);
});

test("technicians without deployment permission see no install entry point", () => {
  renderAt("/", [{ path: "/", element: <SoftwareDeployment me={ME} deviceId={5} /> }]);
  expect(screen.queryByRole("button", { name: /Install software/ })).not.toBeInTheDocument();
});
