import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

test("add a child ticket for another technician; it's listed on the parent", async () => {
  const t = ticket();
  let kids: object[] = [];
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/children`]: () => kids,
    "GET /api/queues": [{ id: 1, name: "Service desk", is_active: true }, { id: 2, name: "Network", is_active: true }],
    "GET /api/queues/2/assignable": [{ id: 7, email: "dcole@corp.local", display_name: "D. Cole" }],
    "POST /api/tickets": ({ body }: Call) => ({ ...t, id: 4900, subject: (body as { subject: string }).subject }),
    "POST /api/tickets/4900/assign": () => {
      kids = [{ id: 4900, label: "#4900", subject: "Patch the uplink", status: "new", priority: "high", queue: { id: 2, name: "Network" }, assignee: { id: 7, email: "dcole@corp.local", display_name: "D. Cole" }, created_at: t.created_at }];
      return t;
    },
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  const section = await screen.findByRole("region", { name: "Child tickets" });
  await userEvent.click(within(section).getByRole("button", { name: "Add a child ticket" }));
  const form = within(section).getByRole("form", { name: "New child ticket" });
  await userEvent.type(within(form).getByLabelText("Subject"), "Patch the uplink");
  await userEvent.selectOptions(within(form).getByLabelText("Queue"), "2");
  await userEvent.selectOptions(await within(form).findByLabelText("Assign to"), await within(form).findByRole("option", { name: "D. Cole" }));
  await userEvent.click(within(form).getByRole("button", { name: "Create child ticket" }));
  expect(await within(section).findByRole("link", { name: "#4900 Patch the uplink" })).toHaveAttribute("href", "/tickets/4900");
  expect(within(section).getByText("D. Cole · Network")).toBeInTheDocument();
  await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/tickets")!.body).toMatchObject({ subject: "Patch the uplink", parent_id: t.id, queue_id: 2 }));
  expect(calls.find((c) => c.path === "/api/tickets/4900/assign")!.body).toMatchObject({ assignee_id: 7 });
});

test("merge into another ticket with a reason, then land on it", async () => {
  const t = ticket();
  const target = ticket({ id: 4700, subject: "Printer down" });
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/children`]: [],
    "GET /api/field-options": [{ id: 31, list: "merge_reason", label: "Duplicate", position: 0, is_active: true, key: null }],
    [`POST /api/tickets/${t.id}/merge`]: target,
    "GET /api/auth/me": ME,
    "GET /api/tickets/4700": target,
  });
  vi.stubGlobal("confirm", () => true);
  renderAt(`/tickets/${t.id}`, [
    { path: "/tickets/:ticketId", element: <TicketPage me={ME} /> },
  ]);
  await userEvent.click(await screen.findByRole("button", { name: "Merge into another ticket…" }));
  const form = screen.getByRole("form", { name: "Merge into another ticket" });
  await userEvent.type(within(form).getByLabelText("Merge into ticket number"), "4700");
  expect(within(form).getByRole("button", { name: "Merge" })).toBeDisabled();
  await userEvent.selectOptions(within(form).getByLabelText("Why"), await within(form).findByRole("option", { name: "Duplicate" }));
  await userEvent.click(within(form).getByRole("button", { name: "Merge" }));
  await waitFor(() => expect(calls.find((c) => c.path === `/api/tickets/${t.id}/merge`)!.body).toEqual({ into_id: 4700, reason_id: 31, note: "" }));
  expect(await screen.findByRole("heading", { name: "Printer down" })).toBeInTheDocument();
});
