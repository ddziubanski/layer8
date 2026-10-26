// SPDX-License-Identifier: AGPL-3.0-or-later
import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Branding, Me } from "../api/client";
import { ME, mockApi, renderAt } from "../test/util";
import { ConsoleLayout } from "./ConsoleLayout";

afterEach(() => vi.unstubAllGlobals());

function mount(me: Me = ME, path = "/") {
  const logout = vi.fn();
  const calls = mockApi({
    "GET /api/about": { product: "Layer 8", version: "1.3.2", source_repository: "https://example.com/source" },
  });
  function Workspace() {
    const [draft, setDraft] = useState("");
    return <ConsoleLayout me={me} brand={{ product_name: "Layer 8" } as Branding} onLogout={logout} logoutPending={false}>
      <input aria-label="Reply draft" value={draft} onChange={event => setDraft(event.target.value)} />
    </ConsoleLayout>;
  }
  renderAt(path, [{ path: "*", element: <Workspace /> }]);
  return { logout, calls, nav: screen.getByRole("navigation", { name: "Main" }) };
}

test("groups modules without bypassing personal or global visibility", async () => {
  const { nav } = mount({ ...ME, hidden_modules: ["kb", "scripts"], disabled_modules: ["devices", "ipam", "settings"] });
  expect(within(nav).getByRole("link", { name: "Tickets" })).toHaveAttribute("href", "/");
  for (const name of ["Knowledge base", "Scripts", "Devices", "IP addresses", "Settings"])
    expect(within(nav).queryByRole("link", { name })).not.toBeInTheDocument();
  expect(within(nav).queryByRole("group", { name: "Infrastructure" })).not.toBeInTheDocument();
  expect(within(nav).getByRole("group", { name: "Service desk" })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: "My preferences" })).toHaveAttribute("href", "/preferences");
  expect(await within(nav).findByRole("link", { name: "Source" })).toHaveAttribute("href", "https://example.com/source");
});

test("keeps administrator recovery access and marks ticket detail as Tickets", () => {
  const { nav } = mount({ ...ME, role: "admin", disabled_modules: ["settings"] }, "/tickets/4821");
  expect(within(nav).getByRole("link", { name: "Settings" })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: "Tickets" })).toHaveAttribute("aria-current", "page");
});

test("collapse and navigation retain the mounted draft without saving preferences", async () => {
  const user = userEvent.setup();
  const { nav, calls, logout } = mount();
  await user.type(screen.getByRole("textbox", { name: "Reply draft" }), "Keep this reply");
  await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
  expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
  await user.click(within(nav).getByRole("link", { name: "Devices" }));
  expect(screen.getByRole("textbox", { name: "Reply draft" })).toHaveValue("Keep this reply");
  expect(within(nav).getByRole("link", { name: "Devices" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("Infrastructure", { selector: ".console-context > span" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Expand navigation" }));
  expect(calls.every(call => call.method === "GET")).toBe(true);
  await user.click(within(nav).getByRole("button", { name: "Sign out" }));
  expect(logout).toHaveBeenCalledOnce();
});

test("responsive compact navigation keeps accessible link names and can be expanded", () => {
  const media = new EventTarget() as MediaQueryList;
  Object.defineProperty(media, "matches", { value: true });
  vi.stubGlobal("matchMedia", () => media);
  const { nav } = mount();
  expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: "IP addresses" })).toHaveAttribute("title", "IP addresses");
  fireEvent.click(screen.getByRole("button", { name: "Expand navigation" }));
  expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeInTheDocument();
  act(() => media.dispatchEvent(Object.assign(new Event("change"), { matches: true })));
  expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
});


test("Settings automatically compacts the main rail and restores the previous state on exit", async () => {
  const user = userEvent.setup();
  const { nav, calls } = mount({ ...ME, role: "admin" });
  await user.type(screen.getByRole("textbox", { name: "Reply draft" }), "Keep this draft");
  await user.click(within(nav).getByRole("link", { name: "Settings" }));
  expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
  await user.click(within(nav).getByRole("link", { name: "Tickets" }));
  expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
  await user.click(within(nav).getByRole("link", { name: "Settings" }));
  await user.click(screen.getByRole("button", { name: "Expand navigation" }));
  await user.click(within(nav).getByRole("link", { name: "Tickets" }));
  expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Reply draft" })).toHaveValue("Keep this draft");
  expect(calls.every(call => call.method === "GET")).toBe(true);
  await user.click(within(nav).getByRole("link", { name: "Settings" }));
  expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
});

test("a direct Settings link starts compact and still offers an accessible way out", async () => {
  const user = userEvent.setup();
  const { nav } = mount({ ...ME, role: "admin" }, "/settings/branding");
  expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
  expect(within(nav).getByRole("link", { name: "Tickets" })).toHaveAttribute("title", "Tickets");
  await user.click(screen.getByRole("button", { name: "Expand navigation" }));
  expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeInTheDocument();
  await user.click(within(nav).getByRole("link", { name: "Tickets" }));
  expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeInTheDocument();
});
