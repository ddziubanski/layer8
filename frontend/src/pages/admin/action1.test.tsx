import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../../test/util";
import { Action1Admin } from "./Action1Admin";

afterEach(() => vi.unstubAllGlobals());

const ADMIN = { ...ME, role: "admin" as const };

const machine = (id: number, name: string) => ({
  id, action1_id: `a${id}`, name, device_id: null, status: "Connected", os: "Windows 11", agent_version: "5.3", user: "", last_seen_at: null, last_boot_at: null,
  missing_critical: 0, missing_other: 0, reboot_required: false, update_status: "", vulnerability_status: "", vulns_critical: 0, vulns_other: 0, organization: "", critical_since: null, reboot_since: null,
  ticket_id: null, in_action1: true, synced_at: "2026-10-20T00:00:00Z",
});

test("Action1: status, test connection, machines without a device", async () => {
  mockApi({
    "GET /api/settings/action1": { section: "action1", fields: [] },
    "GET /api/action1/status": {
      enabled: true, machines: 42, matched: 40, critical: 3, reboot: 5, last_sync_at: "2026-10-20T08:00:00Z", last_sync_ok: true,
      last_sync_summary: "42 machines (40 matched to devices)", last_sync_error: null,
    },
    "GET /api/action1/machines": [machine(1, "KIOSK-01"), machine(2, "LAB-PC")],
    "POST /api/action1/test": { ok: true, error: null, organizations: [{ id: "org-1", name: "Example Org" }], missing_orgs: ["org-9"] },
  });
  renderAt("/settings/action1", [{ path: "/settings/action1", element: <Action1Admin me={ADMIN} /> }]);
  expect(await screen.findByText(/42 machines in Action1, 40 matched/)).toBeInTheDocument();
  expect(await screen.findByText("2 machine(s) in Action1 without a device here")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Example Org (org-1)"));
  expect(screen.getByRole("status")).toHaveTextContent("Not visible to these credentials: org-9.");
});
