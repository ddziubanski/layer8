// SPDX-License-Identifier: AGPL-3.0-or-later
import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  define: { __LAYER8_VERSION__: JSON.stringify(JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).version) },
  server: {
    // The API runs separately in development; the browser sees one origin.
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
