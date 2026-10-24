// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IntegrationHealth } from "./IntegrationHealth";
import { StatusAdmin } from "./StatusAdmin";
import { ME, mockApi, renderAt } from "../../test/util";

const now = new Date().toISOString();
const row = (key: string, state: string, enabled = true) => ({ key, label: key === "email" ? "Microsoft 365 mailbox" : key, state, enabled, configured: true,
  summary: state === "error" ? "The last worker pass failed." : "Saved worker evidence.", next_step: "Review configuration.", settings_path: `/settings/${key}`, job_name: key,
  evidence: "Worker pass", last_attempt_at: now, last_success_at: state === "error" ? null : now, next_due_at: null });
const DATA = { checked_at: now, worker_state: "running", items: [row("email", "ok"), row("meraki", "error"), row("nutanix", "off", false)], relays: [{id: 1, name: "Relay 01", state: "error", last_seen_at: now}] };
afterEach(() => vi.unstubAllGlobals());

function mount() { renderAt("/", [{path:"*", element:<IntegrationHealth />}]); }

test("filters saved health and keeps recovery links and unknown evidence explicit", async () => {
  const user = userEvent.setup();
  const calls = mockApi({"GET /api/system/integrations": DATA});
  mount();
  expect(await screen.findByText("Microsoft 365 mailbox")).toBeInTheDocument();
  expect(screen.queryByText("nutanix", {exact:true})).not.toBeInTheDocument();
  expect(screen.getByText("Last success: Not recorded")).toBeInTheDocument();
  expect(screen.getByRole("link", {name:"Configure meraki"})).toHaveAttribute("href", "/settings/meraki");
  expect(screen.getByText("Relay 01")).toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", {name:"Show integrations"}), "attention");
  expect(screen.queryByText("Microsoft 365 mailbox")).not.toBeInTheDocument();
  expect(screen.getByText("meraki", {exact:true})).toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", {name:"Show integrations"}), "all");
  expect(screen.getByText("nutanix", {exact:true})).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name:"Refresh integration health"}));
  expect(calls.filter(call => call.path === "/api/system/integrations")).toHaveLength(2);
  expect(calls.every(call => call.method === "GET")).toBe(true);
});

test("failed health request can be retried and local-only installs stay useful", async () => {
  let fail = true;
  const user = userEvent.setup();
  mockApi({"GET /api/system/integrations": () => fail ? new Response("{}", {status:503}) : {...DATA,items:[],relays:[],worker_state:"never_started"}});
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
  fail = false;
  await user.click(screen.getByRole("button", {name:"Refresh integration health"}));
  expect(await screen.findByText(/No enabled integrations. Local helpdesk operation remains available/)).toBeInTheDocument();
  expect(screen.getByText("No active relays configured.")).toBeInTheDocument();
  expect(within(screen.getByRole("status")).getByText(/background worker has not checked in/)).toBeInTheDocument();
});

test("technicians do not mount the health panel or request admin status data", () => {
  const calls = mockApi({});
  renderAt("/", [{path:"/", element:<StatusAdmin me={ME} />}]);
  expect(screen.getByText("Status is for admins.")).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});
