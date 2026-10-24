// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from "@testing-library/react";
import { Brand, DEFAULT_LOGO } from "./branding";
import type { Branding } from "./api/client";

const branding = { product_name: "Layer 8", logo_url: null } as Branding;

test("default branding shows the human-and-switch mark on a contrast tile", () => {
  const { container } = render(<Brand b={branding} />);
  expect(screen.getByText("Layer 8")).toBeInTheDocument();
  const logo = container.querySelector("img")!;
  expect(logo).toHaveAttribute("src", DEFAULT_LOGO);
  expect(logo).toHaveClass("brand-logo-default");
  expect(logo).toHaveAttribute("alt", "");
});

test("uploaded customer logos keep their original presentation", () => {
  const { container } = render(<Brand b={{ ...branding, logo_url: "/api/branding/logo" }} />);
  const logo = container.querySelector("img")!;
  expect(logo).toHaveAttribute("src", "/api/branding/logo");
  expect(logo).not.toHaveClass("brand-logo-default");
});
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { contrast, EMBER, MIDNIGHT, PRESETS, themeTokens, ULTRAVIOLET, useBranding } from "./branding";
import { mockApi } from "./test/util";

afterEach(() => vi.unstubAllGlobals());

test.each(Object.entries(PRESETS))("%s text, links and status badges meet AA contrast", (_, p) => {
  for (const [foreground, background] of [
    [p.onBrand, p.brand], [p.ink, p.surface], [p.muted, p.surface], [p.link, p.surface],
    [p.sideInk, p.sidebar], [p.green, p.greenBg], [p.amber, p.amberBg], [p.red, p.redBg],
  ]) expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
});

test("Ember and Ultraviolet apply their own palette in dark mode", () => {
  for (const [name, preset] of [["ember", EMBER], ["ultraviolet", ULTRAVIOLET]] as const) {
    const client = new QueryClient();
    client.setQueryData(["branding"], { theme_preset: name, theme_brand: "#1d4ed8", theme_sidebar: "#111827", dark_mode: "off" });
    function Probe() { useBranding(); return null; }
    const view = render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.preset).toBe(name);
    expect(document.documentElement.style.getPropertyValue("--brand")).toBe(preset.brand);
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe(preset.accent);
    view.unmount();
  }
});

test("switching back from Midnight restores custom colours and light mode", async () => {
  const custom = { theme_preset: "custom", theme_brand: "#1d4ed8", theme_sidebar: "#111827", dark_mode: "off" };
  mockApi({ "GET /api/branding": custom });
  const client = new QueryClient();
  client.setQueryData(["branding"], { ...custom, theme_preset: "midnight" });
  function Probe() { useBranding(); return null; }
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.dataset.preset).toBe("midnight");
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe(MIDNIGHT.brand);
  await client.invalidateQueries({ queryKey: ["branding"] });
  await waitFor(() => expect(document.documentElement.dataset.preset).toBe("custom"));
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe(custom.theme_brand);
  expect(document.documentElement.style.getPropertyValue("--on-brand")).toBe("#ffffff");
  expect(themeTokens(custom.theme_brand, custom.theme_sidebar)["--side"]).toBe(custom.theme_sidebar);
});
