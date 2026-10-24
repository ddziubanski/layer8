// SPDX-License-Identifier: AGPL-3.0-or-later
import { commandsFor } from "./components/CommandPalette";
import { moduleEnabled, visibleModules } from "./navigation";
import { ME } from "./test/util";

test("global modules override personal show-all while personal hidden modules remain in Ctrl+K", () => {
  const me = { ...ME, disabled_modules: ["scripts" as const], hidden_modules: ["reports" as const] };
  expect(visibleModules(me).map(m => m.id)).not.toContain("scripts");
  expect(visibleModules(me).map(m => m.id)).not.toContain("reports");
  expect(commandsFor(me).map(c => c.label)).not.toContain("Scripts");
  expect(commandsFor(me).map(c => c.label)).toContain("Reports");
  expect(commandsFor(me).map(c => c.label)).toContain("Ticket queue");
});

test("admins retain settings recovery when staff Settings is globally off", () => {
  const disabled_modules = ["settings" as const];
  expect(moduleEnabled({ ...ME, disabled_modules }, "settings")).toBe(false);
  const admin = { ...ME, role: "admin" as const, disabled_modules };
  expect(moduleEnabled(admin, "settings")).toBe(true);
  expect(commandsFor(admin).map(c => c.label)).toContain("Settings");
});
