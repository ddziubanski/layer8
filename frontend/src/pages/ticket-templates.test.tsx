// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, ticket, type Call } from "../test/util";
import { NewTicketDialog } from "./NewTicketDialog";
import { TicketTemplatesAdmin } from "./admin/TicketTemplatesAdmin";

const TEMPLATE = { id: 1, name: "New starter", subject: "Prepare laptop", description: "Model:\nStart date:", category: "Hardware", priority: "high", queue_id: 2, queue_name: "IT support", checklist_id: 3, checklist_name: "Starter checklist", is_active: true, updated_at: new Date().toISOString() };
const QUEUES = [{ id: 1, name: "Service desk", is_active: true }, { id: 2, name: "IT support", is_active: true }];
function setup(overrides = {}) {
  const calls = mockApi({
    "GET /api/queues": QUEUES, "GET /api/field-options/department": [],
    "GET /api/ticket-templates": [TEMPLATE],
    "GET /api/users": [{ id: 7, display_name: "Example Requester", email: "requester@example.com", phone: "555-0100", department: null }],
    "POST /api/tickets": ticket({ id: 99 }), ...overrides,
  });
  renderAt("/", [{ path: "/", element: <NewTicketDialog onClose={() => {}} /> }, { path: "/tickets/:ticketId", element: <p>Created ticket</p> }]);
  return calls;
}

test("previewing and cancelling a template changes nothing and makes no writes", async () => {
  const user = userEvent.setup();
  const calls = setup();
  await user.type(screen.getByLabelText("Subject"), "Existing draft");
  await user.selectOptions(await screen.findByLabelText("Start from a template"), "1");
  const preview = screen.getByRole("region", { name: "Template preview" });
  expect(within(preview).getByText("Prepare laptop")).toBeInTheDocument();
  expect(screen.getByLabelText("Subject")).toHaveValue("Existing draft");
  await user.click(within(preview).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("region", { name: "Template preview" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Subject")).toHaveValue("Existing draft");
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("applying fills empty fields while preserving edits, requester and explicit queue/priority", async () => {
  const user = userEvent.setup();
  const calls = setup();
  await user.type(screen.getByLabelText("Subject"), "My subject");
  await user.selectOptions(screen.getByLabelText("Queue"), "1");
  await user.selectOptions(screen.getByLabelText("Priority"), "low");
  await user.selectOptions(screen.getByLabelText("Received via"), "email");
  await user.type(screen.getByRole("combobox", { name: "Requester" }), "Example");
  await user.click(await screen.findByRole("option", { name: /Example Requester/ }));
  await user.selectOptions(await screen.findByLabelText("Start from a template"), "1");
  await user.click(screen.getByRole("button", { name: "Apply template" }));
  expect(screen.getByLabelText("Subject")).toHaveValue("My subject");
  expect(screen.getByLabelText("Details")).toHaveValue(TEMPLATE.description);
  expect(screen.getByLabelText("Queue")).toHaveValue("1");
  expect(screen.getByLabelText("Priority")).toHaveValue("low");
  expect(screen.getByLabelText("Contact phone")).toHaveValue("555-0100");
  expect(screen.getByText(/does not start automatically/)).toBeInTheDocument();
  expect(calls.every(c => c.method === "GET")).toBe(true);
  await user.click(screen.getByRole("button", { name: "Create ticket" }));
  expect(await screen.findByText("Created ticket")).toBeInTheDocument();
  expect(calls.find(c => c.method === "POST")?.body).toMatchObject({ subject: "My subject", description: TEMPLATE.description, category: "Hardware", requester_id: 7, queue_id: 1, priority: "low", source: "email", contact_phone: "555-0100" });
  expect(calls.filter(c => c.method !== "GET")).toHaveLength(1);
});

test("template defaults fill a fresh ticket; an explicitly selected routing choice stays blank", async () => {
  const user = userEvent.setup();
  setup();
  await user.selectOptions(await screen.findByLabelText("Start from a template"), "1");
  await user.selectOptions(screen.getByLabelText("Queue"), "1");
  await user.selectOptions(screen.getByLabelText("Queue"), "");
  await user.click(screen.getByRole("button", { name: "Apply template" }));
  expect(screen.getByLabelText("Subject")).toHaveValue(TEMPLATE.subject);
  expect(screen.getByLabelText("Priority")).toHaveValue("high");
  expect(screen.getByLabelText("Queue")).toHaveValue("");
});

test("a failed template list still permits manual creation", async () => {
  const user = userEvent.setup();
  setup({ "GET /api/ticket-templates": () => new Response(JSON.stringify({ detail: "Unavailable" }), { status: 500 }) });
  expect(await screen.findByText(/Templates could not be loaded/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("Subject"), "Manual ticket");
  await user.click(screen.getByRole("button", { name: "Create ticket" }));
  expect(await screen.findByText("Created ticket")).toBeInTheDocument();
});

test("switching template editors resets their fields and saves the chosen template only", async () => {
  const user = userEvent.setup();
  const other = { ...TEMPLATE, id: 2, name: "VPN issue", subject: "VPN troubleshooting" };
  const calls = mockApi({
    "GET /api/ticket-templates": [TEMPLATE, other], "GET /api/queues": QUEUES, "GET /api/checklist-templates": [],
    "PUT /api/ticket-templates/2": ({ body }: Call) => ({ ...other, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <TicketTemplatesAdmin me={{ ...ME, role: "admin" }} /> }]);
  await user.click(await screen.findByRole("button", { name: "New starter", exact: true }));
  await user.type(screen.getByLabelText("Subject"), " unsaved");
  await user.click(screen.getByRole("button", { name: "VPN issue", exact: true }));
  expect(screen.getByLabelText("Subject")).toHaveValue("VPN troubleshooting");
  await user.click(screen.getByRole("button", { name: "Save", exact: true }));
  await waitFor(() => expect(calls.find(c => c.method === "PUT")?.path).toBe("/api/ticket-templates/2"));
  expect(calls.find(c => c.method === "PUT")?.body).toMatchObject({ name: "VPN issue", subject: "VPN troubleshooting" });
});
