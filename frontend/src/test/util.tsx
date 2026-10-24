// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

import type { Me, Ticket } from "../api/client";

export type Call = { method: string; path: string; body: unknown };
type Handler = (call: Call) => unknown;

/** Replace fetch with a route table: "GET /api/tickets" -> handler. Records every call. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = input.split("?")[0];
    let body: unknown = init.body;
    if (typeof body === "string") body = JSON.parse(body);
    const call = { method, path: input, body };
    calls.push(call);
    const route = routes[`${method} ${path}`];
    if (route === undefined) return new Response(JSON.stringify({ detail: `unmocked ${method} ${input}` }), { status: 404 });
    const result = typeof route === "function" ? (route as Handler)(call) : route;
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result ?? null), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

export function renderAt(path: string, routes: RouteObject[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

export const ME: Me = {
  id: 1,
  email: "jpark@corp.local",
  display_name: "J. Park",
  role: "agent",
  is_active: true,
  auth_source: "local",
  role_source: "manual",
  local_fallback: false,
  has_password: true,
  portal_only: false,
  must_change_password: false,
  ticket_color: null,
  color_rules: [],
  hidden_modules: [],
  ticket_workspace: { mode: "classic", selected_tool: "details", keep_open: false, width: 440 },
  disabled_modules: [],
  // What every technician may do by default (Settings > Permissions).
  permissions: ["ticket_merge", "ticket_bulk", "ad_create_accounts", "kb_publish"],
  // Optional parts in use (Settings > General).
  features: ["action1"],
};

const person = (id: number, name: string) => ({ id, email: `${name.toLowerCase().replace(/\W/g, "")}@corp.local`, display_name: name });

export function ticket(over: Partial<Ticket> = {}): Ticket {
  const created = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  return {
    id: 4821,
    subject: "Switch port flapping, 2nd floor",
    description: "Desks keep losing connection.",
    imported_fields: {},
    category: null,
    source: "email",
    status: "open",
    priority: "high",
    requester: person(7, "R. Alvarez"),
    queue: { id: 2, name: "Network" },
    requested_queue_id: null,
    urgency: null,
    impact: null,
    assignee: null,
    matched_rule_id: 3,
    matched_rule_name: 'subject contains "switch"',
    first_response_due_at: null,
    resolution_due_at: new Date(Date.now() + 42 * 60 * 1000 + 30_000).toISOString(),
    first_responded_at: null,
    resolved_at: null,
    closed_at: null,
    sla_at_risk_at: null,
    child_count: 0,
    open_child_count: 0,
    parent: null,
    attachment_count: 2,
    created_at: created,
    updated_at: created,
    sla_state: "at_risk",
    label: `#${over.id ?? 4821}`,
    ...over,
  };
}
