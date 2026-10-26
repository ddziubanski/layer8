// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { NoticesAdmin } from "../pages/admin/NoticesAdmin";
import { type Call, ME, mockApi, renderAt } from "../test/util";
import { NoticeBanner, NoticeDialog } from "./Notices";

const NOTICE = { id: 7, title: "VPN is flaky", message: "Use Wi-Fi meanwhile.", severity: "warning", audience: "everyone", starts_at: new Date().toISOString(),
  ends_at: null, created_by: null, updated_at: new Date().toISOString(), enabled: true, state: "current" };
const MANAGER = { ...ME, permissions: [...(ME.permissions ?? []), "notices_manage"] };

test("a technician who manages notices turns one off right from the pop-up", async () => {
  let current = [NOTICE];
  const calls = mockApi({
    "GET /api/auth/me": MANAGER,
    "GET /api/notices/current": () => current,
    "PUT /api/notices/7/enabled": ({ body }: Call) => ((current = []), { ...NOTICE, ...(body as object), state: "off" }),
  });
  renderAt("/", [{ path: "/", element: <><NoticeDialog /><NoticeBanner /></> }]);
  const buttons = await screen.findAllByRole("button", { name: "Turn off notice for everyone: VPN is flaky" });
  await userEvent.click(buttons[0]);
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ enabled: false }));
  await waitFor(() => expect(screen.queryByText("VPN is flaky")).not.toBeInTheDocument());
});

test("everyone else only hides it for themselves", async () => {
  mockApi({ "GET /api/auth/me": { ...ME, permissions: [] }, "GET /api/notices/current": [NOTICE] });
  renderAt("/", [{ path: "/", element: <NoticeBanner /> }]);
  expect(await screen.findByRole("button", { name: "Hide notice: VPN is flaky" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Turn off notice/ })).not.toBeInTheDocument();
});

test("Settings > Notices: an on/off switch per notice", async () => {
  const calls = mockApi({ "GET /api/notices": [NOTICE], "PUT /api/notices/7/enabled": { ...NOTICE, enabled: false, state: "off" } });
  renderAt("/", [{ path: "/", element: <NoticesAdmin me={{ ...MANAGER, role: "admin" }} /> }]);
  const toggle = await screen.findByRole("switch", { name: 'Show the notice "VPN is flaky"' });
  expect(toggle).toBeChecked();
  await userEvent.click(toggle);
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")).toMatchObject({ path: "/api/notices/7/enabled", body: { enabled: false } }));
});
