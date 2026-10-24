// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import { createQueryClient } from "./queryClient";
import { ME, mockApi, type Call } from "./test/util";
import type { Me } from "./api/client";

afterEach(() => vi.unstubAllGlobals());

function mount() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <App />
    </QueryClientProvider>,
  );
}

test("global off blocks a bookmarked module before loading its page API", async () => {
  window.history.replaceState(null, "", "/scripts");
  const calls = mockApi({ "GET /api/auth/me": { ...ME, disabled_modules: ["scripts"] }, "GET /api/notices": [] });
  mount();
  expect(await screen.findByRole("heading", { name: "Scripts is turned off" })).toBeInTheDocument();
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  expect(nav.queryByRole("link", { name: "Scripts" })).not.toBeInTheDocument();
  expect(nav.getByRole("link", { name: "Tickets" })).toBeInTheDocument();
  expect(calls.some(c => c.path.startsWith("/api/scripts"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Find a page Ctrl K" }));
  await userEvent.type(screen.getByRole("combobox", { name: "Find a page or ticket number" }), "scripts");
  expect(screen.queryByRole("option", { name: "Scripts" })).not.toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  window.history.replaceState(null, "", "/");
});

test("an admin can disable workspace areas and retain recovery access without reloading", async () => {
  window.history.replaceState(null, "", "/settings/modules");
  let profile: Me = { ...ME, role: "admin", disabled_modules: [] };
  let fields = [
    { name: "workspace_scripts_enabled", kind: "bool", label: "Scripts", value: true, locked: false },
    { name: "workspace_settings_enabled", kind: "bool", label: "Settings for technicians", value: true, locked: false },
  ];
  mockApi({
    "GET /api/auth/me": () => profile, "GET /api/notices": [],
    "GET /api/settings/modules": () => ({ section: "modules", fields }),
    "PUT /api/settings/modules": ({ body }: Call) => {
      const values = (body as { values: Record<string, boolean> }).values;
      fields = fields.map(f => ({ ...f, value: values[f.name] ?? f.value }));
      profile = { ...profile, disabled_modules: ["scripts", "settings"] };
      return { section: "modules", fields };
    },
  });
  mount();
  await screen.findByRole("heading", { name: "Global modules" });
  await userEvent.click(await screen.findByRole("checkbox", { name: "Scripts" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Settings for technicians" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  await waitFor(() => expect(nav.queryByRole("link", { name: "Scripts" })).not.toBeInTheDocument());
  expect(nav.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Global modules" })).toBeInTheDocument();
  window.history.replaceState(null, "", "/");
});

test("signed out: shows the login page without hammering the server", async () => {
  const calls = mockApi({ "GET /api/auth/me": () => new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 }) });
  mount();
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 300));
  // One check (React StrictMode isn't used here). The bug looped thousands of times.
  expect(calls.filter((c) => c.path === "/api/auth/me").length).toBeLessThanOrEqual(2);
});

test("a 401 elsewhere sends you back to the login page once", async () => {
  let signedIn = true;
  const calls = mockApi({
    "GET /api/auth/me": () => (signedIn ? ME : new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 })),
    "GET /api/tickets/views": () => {
      signedIn = false;
      return new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 });
    },
    "GET /api/tickets": () => new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 }),
  });
  mount();
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 300));
  expect(calls.filter((c) => c.path === "/api/auth/me").length).toBeLessThanOrEqual(4);
});

test("signing in shows the console", async () => {
  let signedIn = false;
  mockApi({
    "GET /api/auth/me": () => (signedIn ? ME : new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 })),
    "POST /api/auth/login": () => {
      signedIn = true;
      return ME;
    },
    "GET /api/tickets/views": { unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 },
    "GET /api/tickets": { items: [], total: 0, limit: 50, offset: 0 },
  });
  mount();
  await userEvent.type(await screen.findByLabelText("Email or username"), "agent@corp.local");
  await userEvent.type(screen.getByLabelText("Password"), "agent-test-pass-123");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Ticket queue" })).toBeInTheDocument());
});

test("the first-run admin/admin must choose a new password before anything else", async () => {
  let me = { ...ME, role: "admin" as const, display_name: "Administrator", must_change_password: true };
  const calls = mockApi({
    "GET /api/auth/me": () => me,
    "POST /api/auth/password": () => {
      me = { ...me, must_change_password: false };
      return new Response(null, { status: 204 });
    },
    "GET /api/tickets/views": { unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 },
    "GET /api/tickets": { items: [], total: 0, limit: 50, offset: 0 },
  });
  mount();
  expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Current password"), "admin");
  await userEvent.type(screen.getByLabelText("New password"), "a-much-better-one");
  await userEvent.type(screen.getByLabelText("New password again"), "a-much-better-onf");
  await userEvent.click(screen.getByRole("button", { name: "Save password" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
  await userEvent.clear(screen.getByLabelText("New password again"));
  await userEvent.type(screen.getByLabelText("New password again"), "a-much-better-one");
  await userEvent.click(screen.getByRole("button", { name: "Save password" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Ticket queue" })).toBeInTheDocument());
  expect(calls.find((c) => c.path === "/api/auth/password")?.body).toEqual({ current_password: "admin", new_password: "a-much-better-one" });
});

test("minimal navigation applies on save without rebuilding the route; show all restores it", async () => {
  window.history.replaceState(null, "", "/preferences");
  let profile: Me = { ...ME, hidden_modules: [] };
  mockApi({
    "GET /api/auth/me": () => profile,
    "GET /api/queues": [], "GET /api/notices": [],
    "GET /api/teams/connection": { configured: false, connected: false },
    "PUT /api/auth/me/preferences": ({ body }: Call) => { profile = { ...profile, ...(body as object) }; return profile; },
  });
  mount();
  await screen.findByRole("heading", { name: "My preferences" });
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  expect(nav.getByRole("link", { name: "Scripts" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Use minimal workspace" }));
  expect(nav.getByRole("link", { name: "Scripts" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(nav.queryByRole("link", { name: "Scripts" })).not.toBeInTheDocument());
  for (const name of ["Tickets", "Dashboard", "Devices", "Knowledge base", "My preferences"])
    expect(nav.getByRole("link", { name })).toBeInTheDocument();
  expect(nav.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "My preferences" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Find a page Ctrl K" }));
  await userEvent.type(screen.getByRole("combobox", { name: "Find a page or ticket number" }), "scripts");
  expect(within(screen.getByRole("listbox")).getByRole("option", { name: "Scripts" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Show all modules" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(nav.getByRole("link", { name: "Scripts" })).toBeInTheDocument());
  window.history.replaceState(null, "", "/");
});
