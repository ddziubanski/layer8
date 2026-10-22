import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const BASE = "OU=Groups,DC=corp,DC=example";
const ACCOUNT = {
  enabled: true, problem: null, name: "Pat Lee", dn: "CN=Pat Lee,OU=Staff,DC=corp,DC=example", ou: "OU=Staff,DC=corp,DC=example",
  groups: [
    { dn: `CN=VPN Users,${BASE}`, name: "VPN Users", changeable: true, nested: false, warning: null },
    { dn: "CN=Domain Admins,CN=Users,DC=corp,DC=example", name: "Domain Admins", changeable: false, nested: false, warning: null },
  ],
};
const NESTED = "Nested group: Entra ID won't apply it through nesting.";

function page(extra: Record<string, unknown> = {}) {
  const t = ticket();
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [],
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/scripts": [],
    "GET /api/runs": [],
    "GET /api/ad/ous": [{ dn: "OU=Finance,OU=Staff,DC=corp,DC=example", name: "Finance" }],
    "GET /api/onboarding-templates": [
      { id: 2, name: "Finance – AP clerk", description: "", groups: [{ dn: `CN=Finance,${BASE}`, name: "Finance" }], ou_dn: "OU=Finance,OU=Staff,DC=corp,DC=example",
        department: "Finance", title: "AP clerk", is_active: true },
    ],
    ...extra,
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  return { t, calls };
}

test("apply an onboarding template to the requester: preview with a nesting warning, then run", async () => {
  const t = ticket();
  const { calls } = page({
    [`GET /api/tickets/${t.id}/requester-ad`]: ACCOUNT,
    [`POST /api/tickets/${t.id}/ad-changes/preview`]: {
      add: [{ dn: `CN=Finance,${BASE}`, name: "Finance", changeable: true, nested: true, warning: NESTED }], remove: [], already: [],
      move_to_ou: "OU=Finance,OU=Staff,DC=corp,DC=example", current_ou: "OU=Staff,DC=corp,DC=example", department: "Finance", title: "AP clerk",
    },
    [`POST /api/tickets/${t.id}/ad-changes`]: [],
  });
  const card = await screen.findByRole("region", { name: "AD account" });
  expect(within(card).getByText("VPN Users")).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Change groups or OU…" }));
  // Only groups under the allowed OUs can be removed.
  expect(within(card).getByRole("button", { name: "Remove from VPN Users" })).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Remove from Domain Admins" })).not.toBeInTheDocument();
  await userEvent.selectOptions(await within(card).findByLabelText("Onboarding template"), "2");
  await userEvent.click(within(card).getByRole("button", { name: "Check changes…" }));
  const before = await within(card).findByRole("region", { name: "Before it runs" });
  expect(within(before).getByText(/Entra ID won't apply it/)).toBeInTheDocument();
  await userEvent.click(within(before).getByRole("button", { name: "Make these changes" }));
  await waitFor(() => expect(calls.find((c: Call) => c.method === "POST" && c.path === `/api/tickets/${t.id}/ad-changes`)!.body).toMatchObject({ template_id: 2 }));
});

test("the card stays away when AD changes aren't set up", async () => {
  const t = ticket();
  page({ [`GET /api/tickets/${t.id}/requester-ad`]: { enabled: false, problem: null, name: null, dn: null, ou: null, groups: [] } });
  await screen.findByRole("region", { name: "Scripts" }).catch(() => null);
  await waitFor(() => expect(screen.queryByRole("region", { name: "AD account" })).not.toBeInTheDocument());
});

const DL = { label: "Distribution lists", prefix: "[EMAIL]", groups: [
  { dn: `CN=[EMAIL] All Staff,${BASE}`, name: "[EMAIL] All Staff", changeable: true, nested: false, warning: null },
  { dn: `CN=[EMAIL] Sales,${BASE}`, name: "[EMAIL] Sales", changeable: true, nested: false, warning: null },
] };

test("a new account for a new starter: tick distribution lists, check, create, password shown once", async () => {
  const t = ticket();
  const made = { runs: [], sam: "jsmith2", upn: "jsmith2@example.com", password: "Kx7#pQ2m!Rt9vZ4w" };
  const { calls } = page({
    // The requester (HR, say) has a local account: no card for them, but accounts can still be made.
    [`GET /api/tickets/${t.id}/requester-ad`]: { enabled: true, can_create: true, problem: "Pat isn't an AD account here", name: null, dn: null, ou: null, groups: [] },
    "GET /api/ad/group-lists": [DL],
    [`POST /api/tickets/${t.id}/ad-account/new/preview`]: {
      given_name: "John", surname: "Smith", display_name: "John Smith", sam: "jsmith2", upn: "jsmith2@example.com", mail: "", ou: "OU=Staff,DC=corp,DC=example",
      phone: "+1 555 0100", extension: "4321", mobile: "", department: "", title: "", office: "", company: "", description: "", manager: null,
      groups: [DL.groups[1]], enabled: false, change_password: true, start_date: "2026-11-02", email_manager: true, problems: [],
    },
    [`POST /api/tickets/${t.id}/ad-account/new`]: made,
  });
  const card = await screen.findByRole("region", { name: "AD account" });
  expect(within(card).queryByRole("button", { name: "Change groups or OU…" })).not.toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "New account…" }));
  const form = within(card).getByRole("form", { name: "New AD account" });
  await userEvent.type(within(form).getByLabelText("First name"), "John");
  await userEvent.type(within(form).getByLabelText("Last name"), "Smith");
  await userEvent.type(within(form).getByLabelText("Phone number"), "+1 555 0100");
  await userEvent.type(within(form).getByLabelText("Extension"), "4321");
  await userEvent.type(within(form).getByLabelText(/Start date/), "2026-11-02");
  await userEvent.click(within(form).getByLabelText(/Email the manager/));
  // Distribution lists are tick boxes, shown without their prefix.
  await userEvent.click(await within(form).findByLabelText("Sales"));
  await userEvent.click(within(form).getByRole("button", { name: "Check…" }));
  const before = await within(form).findByRole("region", { name: "Before it's created" });
  expect(within(before).getByText(/jsmith2 · jsmith2@example.com/)).toBeInTheDocument();
  expect(within(before).getByText(/Created disabled, enabled on/)).toBeInTheDocument();
  // The technician assigns the email; the UPN is offered.
  await userEvent.click(within(form).getByRole("button", { name: "Use jsmith2@example.com" }));
  await userEvent.click(within(card).getByRole("button", { name: "Check…" }));
  await userEvent.click(await within(card).findByRole("button", { name: "Create account" }));
  expect(await within(card).findByLabelText("First password")).toHaveTextContent(made.password);
  const sent = calls.find((c: Call) => c.method === "POST" && c.path === `/api/tickets/${t.id}/ad-account/new`)!.body as Record<string, unknown>;
  expect(sent).toMatchObject({ given_name: "John", surname: "Smith", phone: "+1 555 0100", extension: "4321", mail: "jsmith2@example.com", groups: [DL.groups[1].dn], start_date: "2026-11-02", email_manager: true });
});

test("ticking a distribution list on an existing account adds it; unticking one they're in removes it", async () => {
  const t = ticket();
  const inAll = { ...ACCOUNT, groups: [...ACCOUNT.groups, DL.groups[0]] };
  const { calls } = page({
    [`GET /api/tickets/${t.id}/requester-ad`]: inAll,
    "GET /api/ad/group-lists": [DL],
    [`POST /api/tickets/${t.id}/ad-changes/preview`]: { add: [], remove: [], already: [], move_to_ou: null, current_ou: "", department: null, title: null },
  });
  const card = await screen.findByRole("region", { name: "AD account" });
  await userEvent.click(within(card).getByRole("button", { name: "Change groups or OU…" }));
  const all = await within(card).findByLabelText("All Staff");
  expect(all).toBeChecked();
  await userEvent.click(all);
  await userEvent.click(within(card).getByLabelText("Sales"));
  await userEvent.click(within(card).getByRole("button", { name: "Check changes…" }));
  await waitFor(() =>
    expect(calls.find((c: Call) => c.path === `/api/tickets/${t.id}/ad-changes/preview`)!.body).toMatchObject({ add: [DL.groups[1].dn], remove: [DL.groups[0].dn] }),
  );
});

test("copy groups from a colleague: only ones that may change, not ones they're already in", async () => {
  const t = ticket();
  const { calls } = page({
    [`GET /api/tickets/${t.id}/requester-ad`]: ACCOUNT,
    "GET /api/users": [{ id: 9, display_name: "Morgan Osei", email: "mosei@corp.example", role: "requester", is_active: true }],
    "GET /api/ad/users/9/groups": [
      { dn: `CN=VPN Users,${BASE}`, name: "VPN Users", changeable: true, nested: false, warning: null },
      { dn: `CN=Sales Share,${BASE}`, name: "Sales Share", changeable: true, nested: false, warning: null },
      { dn: "CN=Domain Admins,CN=Users,DC=corp,DC=example", name: "Domain Admins", changeable: false, nested: false, warning: null },
    ],
    [`POST /api/tickets/${t.id}/ad-changes/preview`]: { add: [], remove: [], already: [], move_to_ou: null, current_ou: "OU=Staff,DC=corp,DC=example", department: null, title: null },
  });
  const card = await screen.findByRole("region", { name: "AD account" });
  await userEvent.click(within(card).getByRole("button", { name: "Change groups or OU…" }));
  await userEvent.type(within(card).getByRole("combobox", { name: "Copy groups from another person" }), "morgan");
  await userEvent.click(await within(card).findByRole("option", { name: /Morgan Osei/ }));
  expect(await within(card).findByRole("status")).toHaveTextContent("Copied 2 of Morgan Osei's 3 groups (1 left out: technicians can't change it)");
  // VPN Users: already a member, so not added again.
  expect(within(card).getByRole("button", { name: "Don't add Sales Share" })).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Don't add VPN Users" })).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Don't add Domain Admins" })).not.toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Check changes…" }));
  await waitFor(() => expect(calls.find((c) => c.path.endsWith("/ad-changes/preview"))!.body).toMatchObject({ add: [`CN=Sales Share,${BASE}`] }));
});
