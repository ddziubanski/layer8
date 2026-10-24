// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "./App";
import { createQueryClient } from "./queryClient";
import { ME, mockApi } from "./test/util";

const SIGNED_OUT = () => new Response(JSON.stringify({ detail: "Not signed in" }), { status: 401 });
const SSO_FAILED = () => new Response(JSON.stringify({ detail: "This computer didn't offer a Windows sign-in" }), { status: 401 });
const REQUESTER = { ...ME, id: 7, email: "ralvarez@corp.local", display_name: "R. Alvarez", role: "requester" as const, portal_only: true };
const WINDOWS = { windows_sign_in: true, windows_sign_in_silent: true };
const PORTAL = { "GET /api/portal/queues": [], "GET /api/portal/tickets": { items: [], total: 0, limit: 200, offset: 0 } };

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

function mount() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <App />
    </QueryClientProvider>,
  );
}

test("on a work PC the portal signs in with Windows by itself", async () => {
  let me: unknown = null;
  const calls = mockApi({
    ...PORTAL,
    "GET /api/auth/me": () => me ?? SIGNED_OUT(),
    "GET /api/auth/options": WINDOWS,
    "POST /api/auth/sso": () => (me = REQUESTER),
  });
  mount();
  expect(await screen.findByText("R. Alvarez")).toBeInTheDocument();
  expect(calls.filter((c) => c.path === "/api/auth/sso")).toHaveLength(1);
  expect(screen.queryByRole("link", { name: "Sign in to the console" })).not.toBeInTheDocument();
});

test("when Windows sign-in fails, the form offers it as a button and it isn't retried by itself", async () => {
  const calls = mockApi({ "GET /api/auth/me": SIGNED_OUT, "GET /api/auth/options": WINDOWS, "POST /api/auth/sso": SSO_FAILED });
  const first = mount();
  expect(await screen.findByRole("button", { name: "Sign in with your Windows account" })).toBeInTheDocument();
  expect(screen.getByLabelText("Password")).toBeInTheDocument();
  // A silent attempt failing isn't an error to show.
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  first.unmount();

  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Sign in with your Windows account" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("didn't offer a Windows sign-in");
  // Once by itself, once from the click.
  expect(calls.filter((c) => c.path === "/api/auth/sso")).toHaveLength(2);
});

test("no Windows sign-in set up: just the password form", async () => {
  const calls = mockApi({ "GET /api/auth/me": SIGNED_OUT, "GET /api/auth/options": { windows_sign_in: false, windows_sign_in_silent: false } });
  mount();
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByRole("button", { name: /Windows/ })).not.toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/auth/sso")).toBe(false);
});

test("a technician's browser isn't signed in to the portal by itself", async () => {
  let me: unknown = null;
  const calls = mockApi({
    "GET /api/auth/me": () => me ?? SIGNED_OUT(),
    "GET /api/auth/options": WINDOWS,
    "POST /api/auth/login": () => (me = ME),
    "GET /api/tickets/views": { unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 },
    "GET /api/tickets": { items: [], total: 0, limit: 50, offset: 0 },
    "POST /api/auth/logout": () => {
      me = null;
      return new Response(null, { status: 204 });
    },
  });
  const first = mount();
  // Signed out in this tab earlier, so no silent attempt yet.
  sessionStorage.setItem("helpdesk.windowsSignIn.skip", "1");
  await userEvent.type(await screen.findByLabelText("Email or username"), "jpark@corp.local");
  await userEvent.type(screen.getByLabelText("Password"), "agent-test-pass-123");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("heading", { name: "Ticket queue" });
  first.unmount();

  // A new tab: the session memory is gone, but this browser is known to be a technician's.
  sessionStorage.clear();
  me = null;
  mount();
  expect(await screen.findByRole("button", { name: "Sign in with your Windows account" })).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/auth/sso")).toBe(false);
});

test("a technician signed in with Windows gets the portal, and a password sign-in for the console", async () => {
  let me: unknown = { ...ME, portal_only: true };
  mockApi({
    ...PORTAL,
    "GET /api/auth/me": () => me,
    "POST /api/auth/login": () => (me = ME),
    "GET /api/tickets/views": { unassigned: 0, mine: 0, my_queues: 0, sla_at_risk: 0, waiting_on_user: 0, from_email: 0, from_portal: 0 },
    "GET /api/tickets": { items: [], total: 0, limit: 50, offset: 0 },
  });
  const first = mount();
  expect(await screen.findByRole("link", { name: "Sign in to the console" })).toHaveAttribute("href", "/?console-sign-in");
  expect(screen.queryByRole("link", { name: "Back to console" })).not.toBeInTheDocument();
  first.unmount();

  // Following the link (a full page load).
  window.history.replaceState(null, "", "/?console-sign-in");
  mount();
  expect(await screen.findByRole("heading", { name: "Sign in to the console" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Windows/ })).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Email or username"), "jpark@corp.local");
  await userEvent.type(screen.getByLabelText("Password"), "agent-test-pass-123");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Ticket queue" })).toBeInTheDocument());
  expect(window.location.search).toBe("");
});
