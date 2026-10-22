import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { RulesAdmin } from "./RulesAdmin";

const ADMIN = { ...ME, role: "admin" as const };
const CATALOG = {
  ticket_fields: {
    status: { ops: ["in", "not_in"], value: ["new", "open", "waiting_on_user", "resolved", "closed"], label: "Status" },
    status_minutes: { ops: ["gt", "lt"], value: "minutes", label: "Minutes in current status" },
    last_reply_by: { ops: ["eq"], value: ["requester", "agent"], label: "Last public reply by" },
  },
  change_fields: { change: { ops: ["in"], value: ["status", "requester_reply"], label: "What changed (any of)" } },
  actions: {
    add_note: { label: "Add internal note", fields: { body: "template" } },
    set_status: { label: "Set status", fields: { value: ["new", "open", "closed"] } },
  },
  template_vars: ["ticket_id", "subject"],
  sql_enabled: false,
};
const RULE = {
  id: 7, name: "Close resolved", description: null, is_active: true, position: 100, trigger: "schedule", schedule_minutes: 60,
  mode: "builder", conditions: [{ field: "status", op: "in", value: ["resolved"] }], select_sql: null,
  actions: [{ type: "set_status", value: "closed" }], update_sql: null, max_rows: 100, queues: [], next_run_at: null, last_run_at: null,
};

afterEach(() => vi.unstubAllGlobals());

function setup(rules: unknown[] = []) {
  return mockApi({
    "GET /api/ticket-rules/catalog": CATALOG,
    "GET /api/ticket-rules": rules,
    "GET /api/queues": [{ id: 1, name: "Service desk", is_active: true }],
    "GET /api/users": [],
    "POST /api/ticket-rules": { ...RULE, id: 8, name: "Nudge" },
    "GET /api/ticket-rules/7/runs": [],
    "GET /api/ticket-rules/8/runs": [],
    "POST /api/ticket-rules/7/test": {
      id: 1, trigger: "test", dry_run: true, started_at: new Date().toISOString(), finished_at: null, matched: 1, changed: 1, emails: 0, error: null,
      details: [{ ticket_id: 1003, changes: [{ field: "status", before: "resolved", after: "closed" }], notes: 0, emails: 0 }],
    },
  });
}

test("builds a scheduled rule from conditions and actions", async () => {
  const calls = setup();
  renderAt("/", [{ path: "/", element: <RulesAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "New rule" }));
  await userEvent.type(screen.getByLabelText("Name"), "Nudge");
  await userEvent.selectOptions(screen.getByLabelText("Runs"), "schedule");
  await userEvent.type(screen.getByLabelText("Every (minutes)"), "30");
  await userEvent.click(screen.getByRole("button", { name: "Add condition" }));
  await userEvent.click(screen.getByLabelText("resolved"));
  await userEvent.click(screen.getByRole("button", { name: "Add action" }));
  await userEvent.type(screen.getByLabelText("Body"), "Still resolved: ${{ticket_id}");
  // SQL needs the rules login, which this install doesn't have.
  expect(screen.getByRole("button", { name: "SQL" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Save rule" }));

  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/ticket-rules")).toBe(true));
  const body = calls.find((c) => c.method === "POST")!.body as Record<string, unknown>;
  expect(body).toMatchObject({
    name: "Nudge",
    trigger: "schedule",
    schedule_minutes: 30,
    mode: "builder",
    conditions: [{ field: "status", op: "in", value: ["resolved"] }],
    actions: [{ type: "add_note", body: "Still resolved: ${ticket_id}" }],
  });
});

test("change conditions are only offered for on-update rules", async () => {
  setup();
  renderAt("/", [{ path: "/", element: <RulesAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "New rule" }));
  await userEvent.click(screen.getByRole("button", { name: "Add condition" }));
  expect(screen.queryByRole("option", { name: "What changed (any of)" })).toBeNull();
  await userEvent.selectOptions(screen.getByLabelText("Runs"), "on_update");
  expect(screen.getByRole("option", { name: "What changed (any of)" })).toBeInTheDocument();
});

test("test run shows what would change", async () => {
  setup([RULE]);
  renderAt("/", [{ path: "/", element: <RulesAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Test (changes nothing)" }));
  expect(await screen.findByText("Test run (nothing was changed)", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("status: resolved → closed")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "#1003" })).toHaveAttribute("href", "/tickets/1003");
});

test("agents can't manage rules", () => {
  setup();
  renderAt("/", [{ path: "/", element: <RulesAdmin me={ME} /> }]);
  expect(screen.getByText("Ticket rules are managed by admins.")).toBeInTheDocument();
});
