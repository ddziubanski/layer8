// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQueryClient } from "@tanstack/react-query";
import { UpdatesAdmin } from "../pages/admin/UpdatesAdmin";
import type { UpdateStatus } from "../api/client";
import { ME, mockApi, renderAt } from "./util";

const base: UpdateStatus = { current_version: "1.1.0", repository: "https://github.com/ddziubanski/layer8", enabled: true, installer_ready: true, backup_ready: true, busy: false,
  checked_at: "2026-10-24T07:00:00Z", operation: {}, release: { version: "1.2.0", newer: true, verified: true, manifest_sha256: "a".repeat(64), url: "https://github.com/ddziubanski/layer8/releases/tag/v1.2.0", notes: "Release improvements", package_size: 1024 } };
const me = { ...ME, role: "admin" as const };
function Refetch() { const client = useQueryClient(); return <button onClick={() => client.invalidateQueries({ queryKey: ["release-updates"] })}>Simulate API downtime</button>; }

test("loading the page never checks GitHub or installs; install requires review and confirmation", async () => {
  const user = userEvent.setup();
  const calls = mockApi({ "GET /api/system/updates": base, "GET /api/settings/updates": { section: "updates", fields: [] },
    "POST /api/system/updates/apply": { ...base, busy: true, operation: { phase: "queued", message: "Queued" } } });
  renderAt("/", [{ path: "/", element: <UpdatesAdmin me={me} /> }]);
  await screen.findByText("Installed: 1.1.0");
  expect(calls.every(c => c.method === "GET")).toBe(true);
  expect(screen.queryByRole("button", { name: "Back up and install 1.2.0" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review update" }));
  expect(screen.getByRole("button", { name: "Back up and install 1.2.0" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /I have a maintenance window/ }));
  await user.click(screen.getByRole("button", { name: "Back up and install 1.2.0" }));
  await waitFor(() => expect(calls.some(c => c.method === "POST")).toBe(true));
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ version: "1.2.0", manifest_sha256: "a".repeat(64), confirm: true });
});

test("unsigned or older releases never offer installation", async () => {
  mockApi({ "GET /api/system/updates": { ...base, release: { ...base.release, newer: false, verified: false, problem: "No signed update package" } }, "GET /api/settings/updates": { section: "updates", fields: [] } });
  renderAt("/", [{ path: "/", element: <UpdatesAdmin me={me} /> }]);
  expect(await screen.findByText(/No downgrade will be offered/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review update" })).not.toBeInTheDocument();
});

test("sanitized progress and verification remain visible while the API is unavailable", async () => {
  const user = userEvent.setup(); let offline = false;
  const path = "/api/update-progress/" + "b".repeat(32) + ".json";
  mockApi({ "GET /api/system/updates": () => offline ? new Response("{}", { status: 503 }) : base,
    "GET /api/settings/updates": { section: "updates", fields: [] },
    "POST /api/system/updates/apply": () => { offline = true; return { ...base, busy: true, progress_url: path }; },
    [`GET ${path}`]: { phase: "checking", step: 6, steps: 9, message: "Checking the installed application", checks: [{ name: "schema", state: "passed", detail: "Database migration revision verified" }], events: [{ at: "2026-10-24T07:00:00Z", message: "Backup verified" }] } });
  renderAt("/", [{ path: "/", element: <><UpdatesAdmin me={me} /><Refetch /></> }]);
  await user.click(await screen.findByRole("button", { name: "Review update" }));
  await user.click(screen.getByRole("checkbox", { name: /I have a maintenance window/ }));
  await user.click(screen.getByRole("button", { name: "Back up and install 1.2.0" }));
  expect(await screen.findByText(/Database migration revision verified/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Simulate API downtime" }));
  expect(await screen.findByText(/API is paused for maintenance/)).toBeInTheDocument();
  expect(screen.getByText(/Step 6 of 9/)).toBeInTheDocument();
  expect(screen.getByText(/Database migration revision verified/)).toBeInTheDocument();
});
