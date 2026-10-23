import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { ScriptsPage } from "./ScriptsPage";

afterEach(() => vi.unstubAllGlobals());

const ADMIN = { ...ME, role: "admin" as const };
const STALE = {
  id: 1, name: "Devices not seen for a while", description: "Old kit", kind: "query", language: "sql", high_risk: false, agents_can_run: true,
  is_active: true, current_version: 2, body: "SELECT id AS device_id, name FROM devices WHERE last_logon_at < now() - make_interval(days => :days)",
  parameters: [{ name: "days", label: "Not seen for (days)", type: "int", required: true, default: 30, choices: [] }],
  updated_at: "2026-10-19T00:00:00Z", updated_by: { id: 1, email: "admin@corp.local", display_name: "Dev Admin" }, can_run: true, can_edit: true,
};

test("script workspace displays risk and retains run/edit permission gates", async () => {
  mockApi({ "GET /api/scripts": [{ ...STALE, high_risk: true, can_run: false, can_edit: false, agents_can_run: false }] });
  renderAt("/", [{ path: "/", element: <ScriptsPage me={{ ...ME, permissions: [] }} /> }]);
  const pane = await screen.findByRole("region", { name: STALE.name });
  expect(within(pane).getByLabelText("Script state")).toHaveTextContent("High risk");
  expect(within(pane).getByRole("button", { name: /^Run$/ })).toBeDisabled();
  expect(within(pane).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

test("run a query with its parameters, see the result; look at what a version changed", async () => {
  const calls = mockApi({
    "GET /api/scripts": [STALE],
    "POST /api/scripts/1/run": { columns: [{ key: "c0", label: "device_id", kind: "number" }, { key: "c1", label: "name", kind: "text" }], rows: [[7, "WS-OLD"]], totals: {}, truncated: false },
    "GET /api/scripts/1/versions": [
      { number: 2, note: "Oldest first", author: null, created_at: "2026-10-19T00:00:00Z", is_current: true },
      { number: 1, note: "First version", author: null, created_at: "2026-10-18T00:00:00Z", is_current: false },
    ],
    "GET /api/scripts/1/versions/2": { number: 2, note: "Oldest first", author: null, created_at: "2026-10-19T00:00:00Z", is_current: true, body: "x", parameters: [], diff: "--- version 1\n+++ version 2\n-ORDER BY name\n+ORDER BY last_logon_at" },
  });
  renderAt("/", [{ path: "/", element: <ScriptsPage me={ADMIN} /> }]);
  const pane = await screen.findByRole("region", { name: "Devices not seen for a while" });
  const days = within(pane).getByLabelText("Not seen for (days)");
  expect(days).toHaveValue(30);
  await userEvent.clear(days);
  await userEvent.type(days, "60");
  await userEvent.click(within(pane).getByRole("button", { name: "Run" }));
  expect(await within(pane).findByRole("cell", { name: "WS-OLD" })).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ values: { days: "60" } });

  await userEvent.click(within(pane).getByRole("button", { name: "Versions" }));
  await userEvent.click((await within(pane).findAllByRole("button", { name: "Show" }))[0]);
  const v = await within(pane).findByRole("region", { name: "Version 2" });
  expect(within(v).getByText("+ORDER BY last_logon_at")).toHaveClass("diff-add");
  expect(within(v).getByText("-ORDER BY name")).toHaveClass("diff-del");
});

test("admins write a new action script with a parameter", async () => {
  const calls = mockApi({
    "GET /api/scripts": [],
    "POST /api/scripts": ({ body }: Call) => ({ ...STALE, ...(body as object), id: 2, current_version: 1 }),
  });
  renderAt("/", [{ path: "/", element: <ScriptsPage me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "New script" }));
  const form = screen.getByRole("form", { name: "New script" });
  await userEvent.type(within(form).getByLabelText("Name"), "Restart print spooler");
  await userEvent.selectOptions(within(form).getByLabelText("Kind"), "action");
  expect(within(form).getByLabelText("Language")).toHaveValue("powershell");
  await userEvent.type(within(form).getByLabelText(/^Script/), "Restart-Service Spooler");
  await userEvent.click(within(form).getByRole("button", { name: "Add a parameter" }));
  const p = within(form).getByRole("group", { name: "Parameter 1" });
  await userEvent.type(within(p).getByLabelText("Name"), "force");
  await userEvent.selectOptions(within(p).getByLabelText("Type"), "bool");
  await userEvent.click(within(form).getByLabelText(/High risk/));
  await userEvent.click(within(form).getByRole("button", { name: "Create script" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({
      name: "Restart print spooler", kind: "action", language: "powershell", high_risk: true, body: "Restart-Service Spooler",
      parameters: [{ name: "force", type: "bool" }],
    }),
  );
});

const SPOOLER = {
  ...STALE, id: 3, name: "Restart print spooler", kind: "action", language: "powershell", high_risk: true, current_version: 1,
  body: "Restart-Service Spooler", parameters: [],
};
const run = (over = {}) => ({
  id: 41, script_id: 3, script_name: SPOOLER.name, version: 1, language: "powershell", device_id: 5, target: "ws-0142.corp.example", values: {}, ticket_id: null,
  status: "awaiting_approval", requested_by: { id: 2, email: "dcole@corp.local", display_name: "D. Cole" }, decided_by: null, runner_name: null,
  created_at: "2026-10-19T00:00:00Z", claimed_at: null, finished_at: null, exit_code: null, error: null, can_decide: true, can_cancel: true, ...over,
});

test("run an action on a device: check targets first, then it waits for approval; another admin approves", async () => {
  const WS = { id: 5, name: "WS-0142", dns_hostname: "ws-0142.corp.example", os: "Windows 11", assigned_user: null };
  let approved = false;
  const calls = mockApi({
    "GET /api/scripts": [SPOOLER],
    "GET /api/devices": { items: [WS], total: 1 },
    "POST /api/scripts/3/preview": { targets: [{ device_id: 5, name: "WS-0142", target: "ws-0142.corp.example" }], values: {}, needs_approval: true, runners: ["RUNNER01"], runner_online: true },
    "POST /api/scripts/3/runs": [run()],
    "GET /api/runs": () => [run(approved ? { status: "queued", can_decide: false } : {})],
    "POST /api/runs/41/approve": () => ((approved = true), run({ status: "queued" })),
  });
  renderAt("/", [{ path: "/", element: <ScriptsPage me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Run this script" });
  await userEvent.type(within(form).getByRole("combobox", { name: "Add a device" }), "ws-0");
  await userEvent.click(await within(form).findByRole("option", { name: /WS-0142/ }));
  await userEvent.click(within(form).getByRole("button", { name: "Check targets…" }));
  const check = await within(form).findByRole("region", { name: "Before it runs" });
  expect(within(check).getByText(/1 run: ws-0142.corp.example/)).toBeInTheDocument();
  expect(within(check).getByText(/another admin's approval/)).toBeInTheDocument();
  await userEvent.click(within(check).getByRole("button", { name: "Ask for approval" }));
  await waitFor(() => expect(calls.find((c) => c.path === "/api/scripts/3/runs")!.body).toMatchObject({ device_ids: [5], on_runner: false }));
  // It moves to the Runs tab.
  expect(await screen.findByText("Waiting for approval")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText("Queued")).toBeInTheDocument();
});

test("schedule an action on a device every week; the list shows when it runs next and how the last run went", async () => {
  const WS = { id: 5, name: "WS-0142", dns_hostname: "ws-0142.corp.example", os: "Windows 11", assigned_user: null };
  const saved = {
    id: 9, name: "Weekly spooler", script_id: 3, script_name: SPOOLER.name, script_kind: "action", owner: { id: 1, email: "admin@corp.local", display_name: "Dev Admin" },
    values: {}, device_ids: [5], devices: [WS], group_script_id: null, group_name: null, on_runner: false, period: "", recipients: [], message: "",
    frequency: "weekly", weekdays: [2], day_of_month: null, time_of_day: "02:00", start_date: "2026-10-20", is_active: true,
    next_run_at: "2026-10-21T09:00:00Z", last_run_at: null, last_result: "", last_error: null, can_edit: true, recent_runs: [],
  };
  let list: unknown[] = [];
  const calls = mockApi({
    "GET /api/scripts": [SPOOLER],
    "GET /api/devices": { items: [WS], total: 1 },
    "GET /api/script-schedules": () => list,
    "POST /api/script-schedules": () => ((list = [saved]), saved),
    "POST /api/script-schedules/9/run": () => {
      list = [{ ...saved, last_run_at: "2026-10-20T10:00:00Z", last_result: "Queued on 1 device(s), waiting for an admin's approval", recent_runs: [run()] }];
      return list[0];
    },
  });
  renderAt("/", [{ path: "/", element: <ScriptsPage me={ADMIN} /> }]);
  const pane = await screen.findByRole("region", { name: "Restart print spooler" });
  await userEvent.click(within(pane).getByRole("button", { name: "Schedules" }));
  expect(await within(pane).findByText(/each scheduled run still waits for another admin's approval/)).toBeInTheDocument();
  await userEvent.click(within(pane).getByRole("button", { name: "New schedule…" }));
  const form = within(pane).getByRole("form", { name: "New schedule" });
  await userEvent.clear(within(form).getByLabelText("Schedule name"));
  await userEvent.type(within(form).getByLabelText("Schedule name"), "Weekly spooler");
  await userEvent.type(within(form).getByRole("combobox", { name: "Add a device" }), "ws-0");
  await userEvent.click(await within(form).findByRole("option", { name: /WS-0142/ }));
  await userEvent.click(within(form).getByLabelText("Mon"));
  await userEvent.click(within(form).getByLabelText("Wed"));
  await userEvent.click(within(form).getByRole("button", { name: "Save schedule" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/script-schedules")!.body).toMatchObject({
      name: "Weekly spooler", script_id: 3, device_ids: [5], on_runner: false, frequency: "weekly", weekdays: [2], time_of_day: "02:00", day_of_month: null,
    }),
  );
  const row = await within(pane).findByLabelText("Schedule Weekly spooler");
  expect(within(row).getByText(/Every Wed at 02:00 · on WS-0142/)).toBeInTheDocument();
  await userEvent.click(within(row).getByRole("button", { name: "Run now" }));
  expect(await within(pane).findByText(/Last run: Queued on 1 device/)).toBeInTheDocument();
  expect(within(pane).getByLabelText("Recent runs")).toHaveTextContent("ws-0142.corp.example");
});
