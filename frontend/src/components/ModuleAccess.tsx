// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router";
import { api, type Me } from "../api/client";
import { ME_KEY } from "../queryClient";
import { moduleEnabled, moduleForPath, OPTIONAL_MODULES } from "../navigation";

/** Console availability only. Existing API permissions and background jobs stay independent. */
export function ModuleAccess({ me, children }: { me: Me; children: ReactNode }) {
  const profile = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false });
  const active = profile.data?.id === me.id ? profile.data : me;
  const module = moduleForPath(useLocation().pathname);
  // The requester portal is a core helpdesk path; its global flag controls the staff shortcut.
  if (!module || module === "portal" || moduleEnabled(active, module)) return <>{children}</>;
  const label = OPTIONAL_MODULES.find(m => m.id === module)!.label;
  return <section className="card stack" aria-label="Module unavailable">
    <h1>{label} is turned off</h1>
    <p>Your administrator has turned off this workspace module. Its saved data is retained.</p>
    <div className="row-gap"><Link to="/">Return to Tickets</Link>
      {active.role === "admin" && <Link to="/settings/modules">Manage global modules</Link>}
    </div>
  </section>;
}
