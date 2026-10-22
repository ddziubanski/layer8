import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { ChecklistsAdmin } from "./ChecklistsAdmin";

afterEach(() => vi.unstubAllGlobals());

const ADMIN = { ...ME, role: "admin" as const };

test("make an offboarding checklist with a step that disables the account", async () => {
  const calls = mockApi({
    "GET /api/checklist-templates": [],
    "GET /api/queues": [{ id: 4, name: "Facilities" }],
    "GET /api/users": [{ id: 2, display_name: "Tech One", email: "t1@corp.example", role: "agent", is_active: true }],
    "POST /api/checklist-templates": ({ body }: Call) => ({ ...(body as object), id: 1, updated_at: "2026-10-20T00:00:00Z" }),
  });
  renderAt("/settings/checklists", [{ path: "/settings/checklists", element: <ChecklistsAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "New checklist" }));
  const form = screen.getByRole("form", { name: "New checklist" });
  await userEvent.type(within(form).getByLabelText("Name"), "Leaver");
  await userEvent.selectOptions(within(form).getByLabelText("Kind"), "offboarding");
  await userEvent.click(within(form).getByRole("button", { name: "Add a step" }));
  await userEvent.click(within(form).getByRole("button", { name: "Add a step" }));
  const one = within(form).getByRole("group", { name: "Step 1" });
  await userEvent.type(within(one).getByLabelText("Step"), "Collect laptop");
  await userEvent.selectOptions(within(one).getByLabelText("Queue"), await within(one).findByRole("option", { name: "Facilities" }));
  const two = within(form).getByRole("group", { name: "Step 2" });
  await userEvent.type(within(two).getByLabelText("Step"), "Disable account");
  // Offboarding steps count from the last day.
  await userEvent.clear(within(two).getByLabelText("Due (days from the last day)"));
  await userEvent.type(within(two).getByLabelText("Due (days from the last day)"), "1");
  await userEvent.selectOptions(within(two).getByLabelText("It also"), "disable_ad_account");
  await userEvent.selectOptions(within(two).getByLabelText("Technician"), await within(two).findByRole("option", { name: "Tech One" }));
  await userEvent.click(within(form).getByRole("button", { name: "Add checklist" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({
      name: "Leaver", kind: "offboarding",
      items: [{ title: "Collect laptop", queue_id: 4, offset_days: 0, action: null }, { title: "Disable account", offset_days: 1, action: "disable_ad_account", assignee_id: 2 }],
    }),
  );
});
