import type { ReactNode } from "react";
import { useLocation } from "react-router";

const modules = new Set(["devices", "changes", "purchase-orders", "vendors", "ipam", "kb", "scripts", "reports", "preferences", "settings"]);

/** Scope module presentation without changing routing, permissions or panel arrangements. */
export function ModuleWorkspace({ children }: { children: ReactNode }) {
  const module = useLocation().pathname.split("/")[1];
  return modules.has(module) ? <div className={`module-workspace module-${module}`}>{children}</div> : <>{children}</>;
}
