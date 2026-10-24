import type { Me } from "./api/client";

export type SidebarModule = NonNullable<Me["hidden_modules"]>[number];
export const OPTIONAL_MODULES: { id: SidebarModule; label: string; to: string; symbol: string }[] = [
  { id: "dashboard", label: "Dashboard", to: "/dashboard", symbol: "◫" },
  { id: "devices", label: "Devices", to: "/devices", symbol: "▣" },
  { id: "changes", label: "Changes", to: "/changes", symbol: "⇄" },
  { id: "purchase-orders", label: "Purchase orders", to: "/purchase-orders", symbol: "▤" },
  { id: "vendors", label: "Vendors", to: "/vendors", symbol: "◈" },
  { id: "ipam", label: "IP addresses", to: "/ipam", symbol: "⌗" },
  { id: "kb", label: "Knowledge base", to: "/kb", symbol: "▧" },
  { id: "scripts", label: "Scripts", to: "/scripts", symbol: "⌘" },
  { id: "reports", label: "Reports", to: "/reports", symbol: "◫" },
  { id: "settings", label: "Settings", to: "/settings", symbol: "⚙" },
  { id: "portal", label: "Self-service portal", to: "/portal", symbol: "↗" },
];
export const MINIMAL_HIDDEN = OPTIONAL_MODULES.filter(m => !["dashboard", "devices", "kb"].includes(m.id)).map(m => m.id);
export function visibleModules(me: Pick<Me, "hidden_modules">) {
  return OPTIONAL_MODULES.filter(m => !(me.hidden_modules ?? []).includes(m.id));
}
