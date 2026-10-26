// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Branding, Me } from "../api/client";
import { ME, mockApi, renderAt } from "../test/util";
import { ConsoleLayout } from "./ConsoleLayout";
function mount(me: Me = ME, path = "/") {
  const logout = vi.fn();
  const calls = mockApi({ "GET /api/about": { product: "Layer 8", version: "1.8.0", source_repository: "https://example.com/source" } });
  function Workspace() { const [draft, setDraft] = useState(""); return <ConsoleLayout me={me} brand={{ product_name: "Layer 8" } as Branding} onLogout={logout} logoutPending={false}><input aria-label="Reply draft" value={draft} onChange={event => setDraft(event.target.value)} /></ConsoleLayout>; }
  renderAt(path, [{ path: "*", element: <Workspace /> }]);
  return { logout, calls, nav: screen.getByRole("navigation", { name: "Main" }) };
}
test("workspaces respect personal and global visibility", () => {
  const { nav } = mount({ ...ME, hidden_modules: ["kb"], disabled_modules: ["devices", "settings"] });
  expect(within(nav).getByRole("link", { name: "Helpdesk" })).toHaveAttribute("href", "/");
  for (const name of ["Library", "People & devices", "Setup"]) expect(within(nav).queryByRole("link", { name })).not.toBeInTheDocument();
});
test("admin recovery remains available; direct routes select their workspace", () => {
  const { nav } = mount({ ...ME, role: "admin", disabled_modules: ["settings"] }, "/settings/branding");
  expect(within(nav).getByRole("link", { name: "Setup" })).toHaveAttribute("aria-current", "true");
  expect(screen.getByRole("link", { name: /^Settings$/ })).toBeInTheDocument();
});
test("workspace navigation preserves drafts and never saves a layout mode", async () => {
  const user = userEvent.setup(); const { nav, calls, logout } = mount();
  await user.type(screen.getByLabelText("Reply draft"), "Keep this reply");
  await user.click(within(nav).getByRole("link", { name: "People & devices" }));
  expect(screen.getByLabelText("Reply draft")).toHaveValue("Keep this reply");
  expect(screen.getByRole("navigation", { name: "Workspace" })).toHaveTextContent("Devices");
  expect(calls.every(call => call.method === "GET")).toBe(true);
  expect(screen.queryByRole("button", { name: /Collapse navigation/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sign out" }));expect(logout).toHaveBeenCalledOnce();
});
test("organization default is Tidepool and personal appearance is scoped to the console", () => {
  const { container } = { container: document.body };mount({ ...ME, console_appearance: { palette: "terracotta", mode: "dark" } });
  expect(container.querySelector(".material-console")).toHaveAttribute("data-palette", "terracotta");
  expect(container.querySelector(".material-console")).toHaveAttribute("data-appearance", "dark");
});
