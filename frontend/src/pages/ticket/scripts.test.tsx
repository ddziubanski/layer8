// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket, type Call } from "../../test/util";
import { TicketPage } from "../TicketPage";

afterEach(() => vi.unstubAllGlobals());

const WS = {
  id: 5, name: "WS-0142", dns_hostname: "ws-0142.corp.example", source: "directory", directory_dn: null, os: "Windows 11", os_version: null, description: null,
  enabled: true, is_active: true, last_logon_at: null, assigned_user: null, notes: "", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};
const SPOOLER = {
  id: 3, name: "Restart print spooler", description: "Stops and starts the Spooler service", kind: "action", language: "powershell", high_risk: false,
  agents_can_run: true, is_active: true, current_version: 1, body: "Restart-Service Spooler -Force:$force",
  parameters: [{ name: "force", label: "Force", type: "bool", required: false, default: false, choices: [] }], updated_at: "2026-10-19T00:00:00Z",
  updated_by: null, can_run: true, can_edit: false,
};

test("run a script on the ticket's device; it's logged on the ticket", async () => {
  const t = ticket();
  let started = false;
  const run = { id: 9, script_id: 3, script_name: SPOOLER.name, version: 1, language: "powershell", device_id: 5, target: "ws-0142.corp.example", values: { force: true },
    ticket_id: t.id, status: "queued", requested_by: null, decided_by: null, runner_name: null, created_at: "2026-10-19T00:00:00Z", claimed_at: null,
    finished_at: null, exit_code: null, error: null, can_decide: false, can_cancel: true };
  const calls = mockApi({
    [`GET /api/tickets/${t.id}`]: t,
    [`GET /api/tickets/${t.id}/comments`]: [],
    [`GET /api/tickets/${t.id}/attachments`]: [],
    [`GET /api/tickets/${t.id}/events`]: [],
    [`GET /api/tickets/${t.id}/devices`]: [{ device: WS, linked_at: "2026-10-19T00:00:00Z", linked_by: null }],
    "GET /api/devices": { items: [], total: 0 },
    "GET /api/scripts": [SPOOLER, { ...SPOOLER, id: 4, name: "A query", kind: "query", language: "sql" }],
    "GET /api/runs": () => (started ? [run] : []),
    "POST /api/scripts/3/preview": { targets: [{ device_id: 5, name: "WS-0142", target: "ws-0142.corp.example" }], values: { force: true }, needs_approval: false, runners: ["RUNNER01"], runner_online: true },
    "POST /api/scripts/3/runs": () => ((started = true), [run]),
  });
  renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
  const card = await screen.findByRole("region", { name: "Scripts" });
  const pick = within(card).getByLabelText("Run a script");
  // Only actions are offered.
  await waitFor(() => expect(within(pick).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose…", "Restart print spooler"]));
  await userEvent.selectOptions(pick, "3");
  const form = within(card).getByRole("form", { name: "Run this script" });
  // The ticket's device is the starting target.
  expect(within(form).getByText(/WS-0142/)).toBeInTheDocument();
  await userEvent.click(within(form).getByLabelText(/Force/));
  await userEvent.click(within(form).getByRole("button", { name: "Check targets…" }));
  await userEvent.click(await within(form).findByRole("button", { name: "Run now" }));
  await waitFor(() =>
    expect(calls.find((c: Call) => c.path === "/api/scripts/3/runs")!.body).toMatchObject({ device_ids: [5], ticket_id: t.id, values: { force: true } }),
  );
  expect(await within(card).findByText("Runs on this ticket")).toBeInTheDocument();
  expect(within(card).getByText("Queued")).toBeInTheDocument();
});
