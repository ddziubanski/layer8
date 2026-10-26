// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { PreferencesPage } from "./PreferencesPage";

test("personal show-all leaves global-off modules disabled", async () => {
  mockApi({ "GET /api/queues": [], "GET /api/teams/connection": { configured: false, connected: false } });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={{ ...ME, hidden_modules: ["reports"], disabled_modules: ["ipam"] }} /> }]);
  const ipam = screen.getByRole("checkbox", { name: /IP addresses.*turned off by administrator/ });
  expect(ipam).toBeDisabled(); expect(ipam).not.toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Show all modules" }));
  expect(ipam).toBeDisabled(); expect(ipam).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Reports" })).toBeChecked();
});

afterEach(() => vi.unstubAllGlobals());

test("technicians can save any Material palette and reset to the organization default", async () => {
  const calls = mockApi({
    "GET /api/queues": [],
    "GET /api/teams/connection": { configured: false, connected: false },
    "PUT /api/auth/me/preferences": ({ body }: Call) => ({ ...ME, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={ME} /> }]);
  expect(screen.queryByLabelText("Layout")).not.toBeInTheDocument();
  const palette = screen.getByLabelText("Theme");
  expect(within(palette).getAllByRole("option")).toHaveLength(6);
  await userEvent.selectOptions(palette, "graphite-lime");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Appearance" }), "dark");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find(call => call.method === "PUT")?.body).toMatchObject({ console_appearance: { palette: "graphite-lime", mode: "dark" } }));
  await userEvent.click(screen.getByRole("button", { name: "Use organization default" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.filter(call => call.method === "PUT").at(-1)?.body).toMatchObject({ console_appearance: { palette: null, mode: "organization" } }));
});

test("pick my colour and add a rule; saved together", async () => {
  const calls = mockApi({
    "GET /api/queues": [{ id: 2, name: "Network", is_active: true }],
    "PUT /api/auth/me/preferences": ({ body }: Call) => ({ ...ME, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={ME} /> }]);
  await userEvent.click(within(screen.getByRole("group", { name: "My colour" })).getByRole("button", { name: "#059669" }));
  await userEvent.click(screen.getByRole("button", { name: "Add a rule" }));
  const rule = screen.getByRole("group", { name: "Rule 1" });
  await userEvent.selectOptions(within(rule).getByLabelText("When"), "queue");
  await userEvent.selectOptions(within(rule).getByLabelText("Value"), "2");
  await userEvent.click(within(within(rule).getByRole("group", { name: "Rule 1 colour" })).getByRole("button", { name: "#9333ea" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ ticket_color: "#059669", color_rules: [{ field: "queue", value: "2", color: "#9333ea" }], hidden_modules: [] }),
  );
  expect(await screen.findByText("Saved.")).toBeInTheDocument();
});

test("individual module choices and Undo changes preserve the previous selection", async () => {
  const me = { ...ME, hidden_modules: ["scripts" as const] };
  mockApi({ "GET /api/queues": [], "GET /api/teams/connection": { configured: false, connected: false } });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={me} /> }]);
  expect(screen.getByLabelText("Scripts", { exact: true })).not.toBeChecked();
  await userEvent.click(screen.getByLabelText("Reports", { exact: true }));
  expect(screen.getByLabelText("Reports", { exact: true })).not.toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Undo changes" }));
  expect(screen.getByLabelText("Scripts", { exact: true })).not.toBeChecked();
  expect(screen.getByLabelText("Reports", { exact: true })).toBeChecked();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("failed saves keep the workspace draft available for retry", async () => {
  mockApi({ "GET /api/queues": [], "GET /api/teams/connection": { configured: false, connected: false },
    "PUT /api/auth/me/preferences": new Response(JSON.stringify({ detail: "Save failed" }), { status: 503 }) });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={ME} /> }]);
  await userEvent.click(screen.getByRole("button", { name: "Use minimal workspace" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Save failed")).toBeInTheDocument();
  expect(screen.getByLabelText("Scripts", { exact: true })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
});
