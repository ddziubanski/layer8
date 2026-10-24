// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const PC = { id: 5, name: "WS-0142", dns_hostname: "ws-0142.corp.example", os: "Windows 11", last_logon_at: null, is_active: true, assigned_user_id: 3 };

function mount(t: ReturnType<typeof ticket>, extra: Record<string, unknown> = {}) {
  return mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [],
    [`GET /api/tickets/${t.id}/remote-sessions`]: [],
    "GET /api/runs": [],
    "GET /api/scripts": [],
    ...extra,
  });
}

test("no computer yet: pick one of the requester's", async () => {
  const t = ticket({ computer: null });
  const calls = mount(t, {
    "GET /api/devices": { items: [{ ...PC, source: "directory", assigned_user: null }], total: 1 },
    [`PATCH /api/tickets/${t.id}`]: ({ body }: Call) => ({ ...t, computer: (body as { computer_id: number }).computer_id === 5 ? PC : null }),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  const card = await screen.findByRole("region", { name: "Requester's computer" });
  expect(within(card).getByText(/Not set/)).toBeInTheDocument();
  await userEvent.click(await within(card).findByRole("button", { name: "WS-0142" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ computer_id: 5 }));
});

test("Remote Assistance starts a logged session and hands over to the launcher", async () => {
  const t = ticket({ computer: PC });
  const calls = mount(t, {
    [`POST /api/tickets/${t.id}/remote-sessions`]: {
      session: { id: 1, ticket_id: t.id, device_id: 5, target: "ws-0142.corp.example", tool: "msra", started_by: null, started_at: "2026-10-19T00:00:00Z", ended_at: null },
      launch_uri: "helpdesk-remote:offerra?host=ws-0142.corp.example",
    },
  });
  const href = vi.fn();
  vi.stubGlobal("location", { ...window.location, set href(v: string) { href(v); } });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  const card = await screen.findByRole("region", { name: "Requester's computer" });
  expect(within(card).getByRole("link", { name: "WS-0142" })).toHaveAttribute("href", "/devices/5");
  await userEvent.click(within(card).getByRole("button", { name: "Remote Assistance" }));
  await waitFor(() => expect(href).toHaveBeenCalledWith("helpdesk-remote:offerra?host=ws-0142.corp.example"));
  expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/remote-sessions"))).toBe(true);
});
