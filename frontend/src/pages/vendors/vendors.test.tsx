import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";
import { VendorsPage } from "./VendorsPage";

afterEach(() => vi.unstubAllGlobals());

const VENDOR = {
  id: 1, name: "Cisco Meraki", account_number: "EX-00123", website: "", support_phone: "+1 555 0142", support_email: "support@meraki.example",
  support_portal: "https://dashboard.meraki.com", support_hours: "24x7", contract_level: "", contract_ends: null, notes: "", is_active: true,
  contacts: [{ id: 5, name: "Meraki support", role: "support", email: "support@meraki.example", phone: "+1 555 0142", mobile: "", notes: "" }],
  updated_at: "2026-10-19T00:00:00Z", system_count: 1,
};
const SYSTEM = {
  id: 3, name: "Core switching", description: "", notes: "", is_active: true, updated_at: "2026-10-19T00:00:00Z",
  vendor: { id: 1, name: "Cisco Meraki", support_phone: "+1 555 0142", support_email: "support@meraki.example", support_portal: "https://dashboard.meraki.com", support_hours: "24x7", account_number: "EX-00123" },
  contact: VENDOR.contacts[0], devices: [], addresses: [{ id: 1, address: "10.0.0.1", kind: "vip", notes: "" }],
};

test("add a vendor with a contact", async () => {
  const calls = mockApi({ "GET /api/vendors": [], "GET /api/systems": [], "POST /api/vendors": ({ body }: Call) => ({ ...VENDOR, ...(body as object), id: 2 }) });
  renderAt("/vendors", [{ path: "/vendors", element: <VendorsPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Add a vendor" }));
  const form = screen.getByRole("form", { name: "New vendor" });
  await userEvent.type(within(form).getByLabelText("Name"), "Mitel");
  await userEvent.type(within(form).getByLabelText("Support phone"), "+1 800 555 0100");
  await userEvent.click(within(form).getByRole("button", { name: "Add a contact" }));
  const contact = within(form).getByRole("group", { name: "Contact 1" });
  await userEvent.type(within(contact).getByLabelText("Name"), "Pat Lee");
  await userEvent.selectOptions(within(contact).getByLabelText("Role"), "account manager");
  await userEvent.click(within(form).getByRole("button", { name: "Add vendor" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "Mitel", support_phone: "+1 800 555 0100", contacts: [{ name: "Pat Lee", role: "account manager" }] }),
  );
});

test("link a system on a ticket: the vendor's support details are right there", async () => {
  const t = ticket();
  let linked: object[] = [];
  mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/systems`]: () => linked,
    "GET /api/systems": [SYSTEM],
    [`POST /api/tickets/${t.id}/systems`]: () => (linked = [{ system: SYSTEM, linked_at: "2026-10-19T00:00:00Z", linked_by: null }]),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  const card = await screen.findByRole("region", { name: "Systems" });
  await userEvent.type(within(card).getByLabelText("Link a system"), "core");
  await userEvent.click(await within(card).findByRole("option", { name: /Core switching/ }));
  expect((await within(card).findAllByRole("link", { name: "+1 555 0142" }))[0]).toHaveAttribute("href", "tel:+1 555 0142");
  expect(within(card).getByRole("link", { name: "Support portal" })).toHaveAttribute("href", "https://dashboard.meraki.com");
  expect(within(card).getByText(/10.0.0.1 \(VIP \/ virtual\)/)).toBeInTheDocument();
});
