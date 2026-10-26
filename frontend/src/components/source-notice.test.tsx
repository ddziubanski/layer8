// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { mockApi } from "../test/util";
import { SourceNotice } from "./SourceNotice";

afterEach(() => vi.unstubAllGlobals());

const wrap = () => render(<QueryClientProvider client={new QueryClient()}><SourceNotice /></QueryClientProvider>);

test("offers this installation's source and the repository (AGPL section 13)", async () => {
  mockApi({ "GET /api/about": { product: "Layer 8", version: "1.0.0", license: "AGPL-3.0-or-later", source_download: "/api/source", source_repository: "https://github.com/example/layer8" } });
  wrap();
  expect(await screen.findByRole("link", { name: "Source code" })).toHaveAttribute("href", "/api/source");
  expect(screen.getByRole("link", { name: "Repository" })).toHaveAttribute("href", "https://github.com/example/layer8");
  expect(screen.getByRole("link", { name: "AGPL-3.0" })).toBeInTheDocument();
  expect(screen.getByText(/Layer 8 1\.0\.0/)).toBeInTheDocument();
});

test("without a packed archive the repository is the source link", async () => {
  mockApi({ "GET /api/about": { product: "Layer 8", version: "1.0.0", license: "AGPL-3.0-or-later", source_download: null, source_repository: "https://github.com/example/layer8" } });
  wrap();
  expect(await screen.findByRole("link", { name: "Source code" })).toHaveAttribute("href", "https://github.com/example/layer8");
});

test("the sidebar gets one short line", async () => {
  mockApi({ "GET /api/about": { product: "Layer 8", version: "1.0.0", license: "AGPL-3.0-or-later", source_download: "/api/source", source_repository: "https://github.com/example/layer8" } });
  render(<QueryClientProvider client={new QueryClient()}><SourceNotice compact /></QueryClientProvider>);
  expect(await screen.findByRole("link", { name: "Source" })).toHaveAttribute("href", "/api/source");
  expect(screen.queryByRole("link", { name: "Repository" })).not.toBeInTheDocument();
});

test("the collapsed sidebar shows version, licence and source stacked, links kept", async () => {
  mockApi({ "GET /api/about": { product: "Layer 8", version: "1.7.0", license: "AGPL-3.0-or-later", source_download: "/api/source", source_repository: "https://github.com/example/layer8" } });
  render(<QueryClientProvider client={new QueryClient()}><SourceNotice stacked /></QueryClientProvider>);
  expect(await screen.findByText("1.7.0")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "AGPL" })).toHaveAttribute("href", "https://www.gnu.org/licenses/agpl-3.0.html");
  expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute("href", "/api/source");
});
