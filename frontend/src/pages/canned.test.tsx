// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt } from "../test/util";
import { Composer } from "./ticket/Composer";

afterEach(() => vi.unstubAllGlobals());

const REPLY = { id: 3, title: "Restart", body: "Hi {first_name}", to_requester: true, queue_id: null, queue_name: null, personal: false, may_edit: false, updated_at: new Date().toISOString() };

test("a canned reply goes into the composer filled in, and turns it into a reply", async () => {
  const calls = mockApi({
    "GET /api/canned-replies": [REPLY],
    "POST /api/canned-replies/3/render": { body: "Hi Ana, please restart.", to_requester: true },
  });
  renderAt("/", [{ path: "/", element: <Composer ticketId={4821} onDone={() => undefined} /> }]);
  await userEvent.type(screen.getByLabelText("Work notes and replies"), "Thanks for the details.");
  await userEvent.selectOptions(await screen.findByLabelText("Insert a canned reply"), "3");
  await waitFor(() => expect(screen.getByLabelText("Work notes and replies")).toHaveValue("Thanks for the details.\n\nHi Ana, please restart."));
  expect(screen.getByLabelText("Show to the requester in the portal")).toBeChecked();
  expect(calls.some((c) => c.path.includes("ticket_id=4821"))).toBe(true);
});
