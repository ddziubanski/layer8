// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { type Call, mockApi, renderAt, ticket } from "../../test/util";
import { UpdateEveryone } from "./UpdateEveryone";

const row = (id: number, subject: string, emailed: boolean, why: string | null, resolved = true, note: string | null = null) => ({
  ticket: { id, label: `#${id}`, subject, status: "open", priority: "medium", queue: { id: 1, name: "Service desk" }, assignee: null, created_at: "2026-10-20T09:00:00Z" },
  requester: "R. Alvarez", emailed, why_no_email: why, resolved, note,
});
const PREVIEW = { applied: false, fingerprint: "abc", resolve: true, emails: 1, skipped_closed: 1,
  tickets: [row(4822, "No network at desk 4", true, null), row(4821, "Switch down", false, "already emailed for another of these tickets", false, "Left open: it has other open child tickets that aren't part of this update")] };

test("preview first, then send what was reviewed; resolving shows what stays open", async () => {
  let done = false;
  const calls = mockApi({
    "GET /api/field-options": [{ id: 1, list: "resolution_type", label: "Hardware replaced", is_active: true }],
    "POST /api/tickets/4821/broadcast/preview": PREVIEW,
    "POST /api/tickets/4821/broadcast": ({ body }: Call) => ({ ...PREVIEW, applied: true, echo: body }),
  });
  renderAt("/", [{ path: "/", element: <UpdateEveryone ticket={ticket()} onDone={() => { done = true; }} /> }]);
  await userEvent.type(screen.getByRole("textbox", { name: /Update for everyone affected/ }), "Switch replaced");
  await userEvent.click(screen.getByRole("checkbox", { name: "Resolve them all" }));
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Resolution type" }), "1");
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(await screen.findByText(/No: already emailed/)).toBeInTheDocument();
  expect(screen.getByText(/Left open/)).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/tickets/4821/broadcast")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Send to 1 person and resolve 1" }));
  await waitFor(() => expect(done).toBe(true));
  expect(calls.find((c) => c.path === "/api/tickets/4821/broadcast")?.body).toEqual(
    { body: "Switch replaced", resolve: true, resolution_type_id: 1, closure_reason_id: null, fingerprint: "abc", confirm: true });
});

test("changing the message after a preview needs a new preview", async () => {
  mockApi({ "POST /api/tickets/4821/broadcast/preview": { ...PREVIEW, resolve: false } });
  renderAt("/", [{ path: "/", element: <UpdateEveryone ticket={ticket()} onDone={() => {}} /> }]);
  const box = screen.getByRole("textbox", { name: /Update for everyone affected/ });
  await userEvent.type(box, "First");
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByRole("button", { name: /Send to 1 person/ });
  await userEvent.type(box, " edit");
  expect(screen.queryByRole("button", { name: /Send to/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
});
