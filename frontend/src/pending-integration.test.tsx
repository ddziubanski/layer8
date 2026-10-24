import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { createQueryClient } from "./queryClient";
import { ME, mockApi, type Call } from "./test/util";
import type { Me } from "./api/client";

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

test("Midnight and minimal navigation persist together while hidden modules remain keyboard-accessible", async () => {
  let profile: Me = { ...ME, hidden_modules: [] };
  mockApi({
    "GET /api/auth/me": () => profile,
    "GET /api/branding": { product_name: "Layer 8", theme_preset: "midnight", dark_mode: "off" },
    "GET /api/queues": [], "GET /api/notices": [],
    "GET /api/teams/connection": { configured: false, connected: false },
    "GET /api/scripts": [],
    "PUT /api/auth/me/preferences": ({ body }: Call) => { profile = { ...profile, ...(body as object) }; return profile; },
  });
  const mount = () => render(<QueryClientProvider client={createQueryClient()}><App /></QueryClientProvider>);
  window.history.replaceState(null, "", "/preferences");
  let view = mount();
  await screen.findByRole("heading", { name: "My preferences" });
  await waitFor(() => expect(document.documentElement.dataset.preset).toBe("midnight"));
  await userEvent.click(screen.getByRole("button", { name: "Use minimal workspace" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  await waitFor(() => expect(nav.queryByRole("link", { name: "Scripts" })).not.toBeInTheDocument());
  await userEvent.keyboard("{Control>}k{/Control}");
  await userEvent.type(await screen.findByRole("combobox", { name: "Find a page or ticket number" }), "scripts");
  expect(screen.getByRole("option", { name: "Scripts" })).toBeInTheDocument();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "Scripts" })).toBeInTheDocument();
  expect(document.documentElement.dataset.theme).toBe("dark");
  await userEvent.click(nav.getByRole("link", { name: "My preferences" }));
  expect(await screen.findByLabelText("Scripts", { exact: true })).not.toBeChecked();
  view.unmount();
  view = mount();
  expect(await screen.findByLabelText("Scripts", { exact: true })).not.toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Show all modules" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Scripts" })).toBeInTheDocument());
  view.unmount();
  view = mount();
  expect(await screen.findByLabelText("Scripts", { exact: true })).toBeChecked();
});
