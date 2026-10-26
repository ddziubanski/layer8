// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { NewTicketDialog } from "../NewTicketDialog";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const opt = (id: number, list: string, label: string) => ({ id, list, label, position: id, is_active: true, key: null });
const OPTIONS = {
  resolution_type: [opt(1, "resolution_type", "Fixed"), opt(2, "resolution_type", "Workaround")],
  closure_reason: [opt(3, "closure_reason", "Resolved"), opt(4, "closure_reason", "Duplicate")],
  department: [opt(5, "department", "Finance"), opt(6, "department", "IT")],
};
const MOSEI = { ...ME, id: 9, email: "mosei@corp.local", display_name: "M. Osei", phone: "+1 555 0199", department: "it" };

async function mount(over = {}) {
  const t = ticket({ contact_phone: "+1 555 0100", department: { id: 5, label: "Finance" }, resolution_type: null, closure_reason: null, ...over });
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    "GET /api/queues": [{ id: 2, name: "Network", is_active: true }],
    "GET /api/queues/2/assignable": [],
    "GET /api/field-options": ({ path }: Call) => OPTIONS[new URLSearchParams(path.split("?")[1]).get("list") as keyof typeof OPTIONS],
    "GET /api/users": [MOSEI],
    [`PATCH /api/tickets/${t.id}`]: ({ body }: Call) => ({ ...t, ...(body as object) }),
    [`POST /api/tickets/${t.id}/requester`]: { ...t, requester: { id: 9, email: MOSEI.email, display_name: MOSEI.display_name } },
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("tab", { name: "Details" }));
  return { t, calls };
}

test("resolving asks for the resolution type and closure reason", async () => {
  const { t, calls } = await mount();
  const status = await screen.findByRole("combobox", { name: "Status" });
  await waitFor(() => expect(calls.some((c) => c.path.includes("list=closure_reason"))).toBe(true));
  await userEvent.selectOptions(status, "resolved");
  const form = await screen.findByRole("form", { name: "Resolution" });
  await userEvent.selectOptions(within(form).getByLabelText("Resolution type"), "Fixed");
  await userEvent.selectOptions(within(form).getByLabelText("Closure reason"), "Resolved");
  await userEvent.click(within(form).getByRole("button", { name: "Resolve ticket" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PATCH" && c.path === `/api/tickets/${t.id}`)?.body).toEqual({ status: "resolved", resolution_type_id: 1, closure_reason_id: 3 }),
  );
});

test("change the requester by picking them; edit phone and department", async () => {
  const { t, calls } = await mount();
  await userEvent.click(await screen.findByRole("button", { name: "Change requester" }));
  await userEvent.type(screen.getByRole("combobox", { name: "Change requester to" }), "osei");
  await userEvent.click(await screen.findByRole("option", { name: /M. Osei/ }));
  await waitFor(() => expect(calls.find((c) => c.path === `/api/tickets/${t.id}/requester`)?.body).toEqual({ user_id: 9 }));

  const phone = screen.getByLabelText("Contact phone");
  expect(phone).toHaveValue("+1 555 0100");
  await userEvent.clear(phone);
  await userEvent.type(phone, "ext. 4410");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ contact_phone: "ext. 4410" }));
  await userEvent.selectOptions(screen.getByLabelText("Department"), "IT");
  await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ department_id: 6 }));
});

test("new ticket: pick the requester, and their phone and department fill in from AD", async () => {
  const calls = mockApi({
    "GET /api/queues": [],
    "GET /api/field-options": OPTIONS.department,
    "GET /api/users": [MOSEI],
    "POST /api/tickets": ticket(),
  });
  renderAt("/", [{ path: "/", element: <NewTicketDialog onClose={() => {}} /> }, { path: "/tickets/:id", element: <p>opened</p> }]);
  await userEvent.type(screen.getByLabelText("Subject"), "Laptop won't boot");
  await waitFor(() => expect(calls.some((c) => c.path.includes("field-options"))).toBe(true));
  await userEvent.type(screen.getByRole("combobox", { name: "Requester" }), "osei");
  await userEvent.click(await screen.findByRole("option", { name: /M. Osei/ }));
  expect(screen.getByLabelText("Contact phone")).toHaveValue("+1 555 0199");
  expect(screen.getByLabelText("Department")).toHaveValue("6"); // "it" matched IT
  await userEvent.click(screen.getByRole("button", { name: "Create ticket" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ requester_id: 9, contact_phone: "+1 555 0199", department_id: 6 }),
  );
});

test("imported details show only on tickets that have them", async () => {
  await mount({ imported_fields: { Building: "A", "Ticket source": "Phone" } });
  const box = await screen.findByRole("region", { name: "Imported details" });
  expect(box).toHaveTextContent("BuildingA");
  expect(box).toHaveTextContent("Ticket sourcePhone");
});

test("no imported details box on other tickets", async () => {
  await mount();
  await screen.findByRole("combobox", { name: "Status" });
  expect(screen.queryByRole("region", { name: "Imported details" })).toBeNull();
});
