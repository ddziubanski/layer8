// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../test/util";
import { ReportsPage } from "./ReportsPage";

afterEach(() => vi.unstubAllGlobals());

test("empty report library retains schedules and distinguishes a saved-report failure", async () => {
  mockApi({ "GET /api/reports/presets": [], "GET /api/reports/saved": [] });
  const view = renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  expect(await screen.findByText(/No reports are available yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Scheduled reports" })).toBeInTheDocument();
  view.unmount();
  mockApi({ "GET /api/reports/presets": [], "GET /api/reports/saved": new Response('{"detail":"unavailable"}', { status: 503 }) });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  expect(await screen.findByRole("alert")).toHaveTextContent("Saved reports could not be loaded");
  expect(screen.queryByText(/No reports are available yet/)).not.toBeInTheDocument();
});

const PRESETS = [
  { key: "total-by-status", title: "Total tickets by status", description: "How many…", group: "Tickets", spec: { dataset: "tickets", group_by: ["status"], measures: ["count"], filters: {}, sort: "measure", limit: 200 } },
  {
    key: "per-technician",
    title: "Tickets per technician",
    description: "Assigned, still open…",
    group: "Technicians",
    spec: { dataset: "tickets", group_by: ["assignee"], measures: ["count", "avg_hours_to_resolve"], filters: {}, sort: "measure", limit: 200 },
  },
];
const DATASET = { name: "tickets", label: "Tickets", dimensions: [], measures: [], filters: [], dates: [{ key: "created", label: "Opened" }, { key: "resolved", label: "Resolved" }] };

test("pick a ready-made report and a period; read the table and download it", async () => {
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets/tickets": DATASET,
    "POST /api/reports/run": ({ body }: Call) =>
      (body as { group_by: string[] }).group_by[0] === "assignee"
        ? {
            columns: [
              { key: "assignee", label: "Technician", kind: "text" },
              { key: "count", label: "Tickets", kind: "count" },
              { key: "avg_hours_to_resolve", label: "Avg. hours to resolve", kind: "hours" },
            ],
            rows: [
              ["J. Park", 12, 5.5],
              ["Unassigned", 3, null],
            ],
            totals: { count: 15 },
            truncated: false,
          }
        : { columns: [{ key: "status", label: "Status", kind: "text" }, { key: "count", label: "Tickets", kind: "count" }], rows: [["open", 4]], totals: { count: 4 }, truncated: false },
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  expect(await screen.findByRole("heading", { name: "Total tickets by status" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Tickets per technician" }));
  const table = await screen.findByRole("table");
  expect(await within(table).findByText("J. Park")).toBeInTheDocument();
  expect(within(table).getByText("5.5 h")).toBeInTheDocument();
  expect(within(table).getByText("–")).toBeInTheDocument(); // no resolved tickets
  expect(within(table).getByText("15")).toBeInTheDocument(); // total

  // Default period: the last 30 days by opened date; change to all time by resolved date.
  const first = calls.find((c) => c.path === "/api/reports/run")!.body as Record<string, unknown>;
  expect(first.date_field).toBe("created");
  expect(first.date_from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  await userEvent.selectOptions(screen.getByLabelText("Period"), "all");
  await userEvent.selectOptions(screen.getByLabelText("Which date"), "resolved");
  await waitFor(() => {
    const last = calls.filter((c) => c.path === "/api/reports/run").at(-1)!.body as Record<string, unknown>;
    expect(last.date_field).toBe("resolved");
    expect(last.date_from).toBeUndefined();
  });
});

test("customize a report and save it for everyone", async () => {
  const DS = {
    ...DATASET,
    dimensions: [
      { key: "status", label: "Status", kind: "text" },
      { key: "assignee", label: "Technician", kind: "text" },
      { key: "priority", label: "Priority", kind: "text" },
    ],
    measures: [
      { key: "count", label: "Tickets", kind: "count" },
      { key: "open", label: "Still open", kind: "count" },
    ],
    filters: [
      {
        key: "priority",
        label: "Priority",
        choices: "enum:priority",
        values: [
          { value: "high", label: "High" },
          { value: "critical", label: "Critical" },
        ],
      },
    ],
  };
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets/tickets": DS,
    "GET /api/reports/saved": [],
    "POST /api/reports/run": { columns: [{ key: "status", label: "Status", kind: "text" }, { key: "count", label: "Tickets", kind: "count" }], rows: [], totals: {}, truncated: false },
    "POST /api/reports/saved": ({ body }: Call) => ({ id: 3, description: "", owner: null, can_edit: true, updated_at: new Date().toISOString(), ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
  const builder = screen.getByRole("group", { name: "Customize report" });
  await userEvent.selectOptions(within(builder).getByLabelText("Then by"), "priority");
  await userEvent.click(within(builder).getByLabelText("Still open"));
  await userEvent.click(within(builder).getByText(/Filters/));
  await userEvent.click(within(builder).getByLabelText("Critical"));
  await waitFor(() => {
    const last = calls.filter((c) => c.path === "/api/reports/run").at(-1)!.body as Record<string, unknown>;
    expect(last).toMatchObject({ group_by: ["status", "priority"], measures: ["count", "open"], filters: { priority: ["critical"] } });
  });
  expect(screen.getByText("(changed)")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Save as new report…" }));
  const form = screen.getByRole("form", { name: "Save report" });
  await userEvent.clear(within(form).getByLabelText("Name"));
  await userEvent.type(within(form).getByLabelText("Name"), "Critical by status");
  await userEvent.click(within(form).getByLabelText(/Share with everyone/));
  await userEvent.click(within(form).getByRole("button", { name: "Save report" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/reports/saved")?.body).toMatchObject({
      name: "Critical by status",
      shared: true,
      spec: { group_by: ["status", "priority"], filters: { priority: ["critical"] } },
    }),
  );
});

const RUN = { columns: [{ key: "status", label: "Status", kind: "text" }, { key: "count", label: "Tickets", kind: "count" }], rows: [["Open", 4]], totals: { count: 4 }, truncated: false };

test("email a report now to people picked or typed in", async () => {
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets/tickets": DATASET,
    "GET /api/reports/saved": [],
    "POST /api/reports/run": RUN,
    "GET /api/users": [{ ...ME, id: 9, email: "mosei@example.com", display_name: "M. Osei" }],
    "POST /api/reports/email": { queued: 2 },
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "Email or schedule…" }));
  const panel = screen.getByRole("form", { name: "Email or schedule" });
  await userEvent.type(within(panel).getByRole("combobox", { name: "Send to" }), "osei");
  await userEvent.click(await within(panel).findByRole("option", { name: /M. Osei/ }));
  await userEvent.type(within(panel).getByLabelText("Email address"), "boss@example.com{Enter}");
  expect(within(panel).getByRole("list", { name: "Recipients" })).toHaveTextContent("mosei@example.com");
  await userEvent.click(within(panel).getByLabelText(/PDF and CSV/));
  await userEvent.click(within(panel).getByRole("button", { name: "Send report" }));
  expect(await within(panel).findByText(/Sent to 2 people/)).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/reports/email")?.body).toMatchObject({
    title: "Total tickets by status",
    recipients: ["mosei@example.com", "boss@example.com"],
    format: "both",
    spec: { group_by: ["status"] },
  });
});

test("schedule a report weekly, then see it under Scheduled reports", async () => {
  const SCHEDULE = {
    id: 4, name: "Monday status", preset_key: "total-by-status", saved_report_id: null, report_title: "Total tickets by status",
    period: "previous_week", recipients: ["boss@example.com"], format: "pdf", message: "", frequency: "weekly", weekdays: [0, 3],
    day_of_month: null, time_of_day: "07:30", start_date: "2026-10-19", is_active: true, next_run_at: "2026-10-22T14:30:00Z",
    last_run_at: null, last_error: null, owner: { id: 1, email: ME.email, display_name: "J. Park" }, can_edit: true,
  };
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets/tickets": DATASET,
    "GET /api/reports/saved": [],
    "POST /api/reports/run": RUN,
    "GET /api/reports/periods": { previous_week: "Previous week (Mon–Sun)", previous_month: "Previous month" },
    "POST /api/reports/schedules": SCHEDULE,
    "GET /api/reports/schedules": [SCHEDULE],
    "POST /api/reports/schedules/4/send": { queued: 1 },
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "Email or schedule…" }));
  const panel = screen.getByRole("form", { name: "Email or schedule" });
  await userEvent.click(within(panel).getByRole("button", { name: "Schedule" }));
  await userEvent.type(within(panel).getByLabelText("Email address"), "boss@example.com{Enter}");
  await userEvent.clear(within(panel).getByLabelText("Schedule name"));
  await userEvent.type(within(panel).getByLabelText("Schedule name"), "Monday status");
  await userEvent.click(within(panel).getByLabelText("Thu"));
  await userEvent.click(within(panel).getByRole("button", { name: "Save schedule" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/reports/schedules")?.body).toMatchObject({
      name: "Monday status",
      preset_key: "total-by-status",
      saved_report_id: null,
      frequency: "weekly",
      weekdays: [0, 3],
      day_of_month: null,
      recipients: ["boss@example.com"],
    }),
  );
  const list = await screen.findByRole("region", { name: "Scheduled reports" });
  expect(await within(list).findByText("Every Mon, Thu at 07:30", { exact: false })).toBeInTheDocument();
  await userEvent.click(within(list).getByRole("button", { name: "Send now" }));
  expect(await within(list).findByText(/Sent. It goes out/)).toBeInTheDocument();
});

test("report on other data: switching starts the report fresh and uses that data's dates", async () => {
  const ACTIVITY = {
    name: "activity",
    label: "Ticket activity",
    dimensions: [{ key: "author", label: "Who", kind: "text" }],
    measures: [{ key: "count", label: "Replies and notes", kind: "count" }],
    filters: [],
    dates: [{ key: "created", label: "Written" }],
  };
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets": [
      { name: "tickets", label: "Tickets" },
      { name: "activity", label: "Ticket activity" },
    ],
    "GET /api/reports/datasets/tickets": DATASET,
    "GET /api/reports/datasets/activity": ACTIVITY,
    "GET /api/reports/saved": [],
    "POST /api/reports/run": { columns: [{ key: "count", label: "Replies and notes", kind: "count" }], rows: [[3]], totals: {}, truncated: false },
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
  const builder = screen.getByRole("group", { name: "Customize report" });
  await waitFor(() => expect(within(builder).getByRole("option", { name: "Ticket activity" })).toBeInTheDocument());
  await userEvent.selectOptions(within(builder).getByLabelText("Report on"), "activity");
  await waitFor(() => {
    const last = calls.filter((c) => c.path === "/api/reports/run").at(-1)!.body as Record<string, unknown>;
    expect(last).toMatchObject({ dataset: "activity", group_by: [], measures: ["count"], filters: {}, date_field: "created" });
  });
  expect(screen.getByLabelText("Which date")).toHaveDisplayValue("written");
});

test("SQL report: the query runs when asked, text-only results show without bars", async () => {
  const SQL = { name: "sql", label: "SQL query", dimensions: [], measures: [], filters: [], dates: [{ key: "period", label: "Period" }] };
  const calls = mockApi({
    "GET /api/reports/presets": PRESETS,
    "GET /api/reports/datasets": [
      { name: "tickets", label: "Tickets" },
      { name: "sql", label: "SQL query" },
    ],
    "GET /api/reports/datasets/tickets": DATASET,
    "GET /api/reports/datasets/sql": SQL,
    "GET /api/reports/saved": [],
    "POST /api/reports/run": { columns: [{ key: "c0", label: "subject", kind: "text" }], rows: [["Printer jam"]], totals: {}, truncated: false },
  });
  renderAt("/", [{ path: "/", element: <ReportsPage /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
  const builder = screen.getByRole("group", { name: "Customize report" });
  await waitFor(() => expect(within(builder).getByRole("option", { name: "SQL query" })).toBeInTheDocument());
  await userEvent.selectOptions(within(builder).getByLabelText("Report on"), "sql");
  const box = await screen.findByRole("textbox", { name: "SQL query" });
  const sqlBuilder = screen.getByRole("group", { name: "Customize report" });
  expect(within(sqlBuilder).queryByLabelText("Group by")).not.toBeInTheDocument();
  expect((box as HTMLTextAreaElement).value).toContain(":start");
  await userEvent.clear(box);
  await userEvent.type(box, "SELECT subject FROM tickets");
  const runs = () => calls.filter((c) => c.path === "/api/reports/run").length;
  const before = runs();
  await userEvent.click(within(sqlBuilder).getByRole("button", { name: "Run query" }));
  await waitFor(() => expect(runs()).toBe(before + 1));
  expect(calls.filter((c) => c.path === "/api/reports/run").at(-1)!.body).toMatchObject({ dataset: "sql", sql: "SELECT subject FROM tickets", date_field: "period" });
  expect(await screen.findByRole("cell", { name: "Printer jam" })).toBeInTheDocument();
});
