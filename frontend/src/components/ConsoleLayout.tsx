// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import type { Branding, Me } from "../api/client";
import { Brand, backgroundStyle } from "../branding";
import { OPTIONAL_MODULES, visibleModules } from "../navigation";
import { CommandPalette } from "./CommandPalette";
import { SourceNotice } from "./SourceNotice";
import { UnacceptedCounter } from "./UnacceptedCounter";

const GROUPS = [
  { label: "Service desk", ids: ["tickets", "dashboard", "kb"] },
  { label: "Infrastructure", ids: ["devices", "ipam", "scripts"] },
  { label: "Operations", ids: ["changes", "purchase-orders", "vendors", "reports"] },
  { label: "Workspace", ids: ["settings", "portal"] },
];
const ICONS: Record<string, ReactNode> = {
  tickets: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 9h10M7 13h7" /></>,
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  devices: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 21h8M12 16v5" /></>,
  changes: <><path d="M4 7h15l-4-4M20 17H5l4 4M19 7l-4 4M5 17l4-4" /></>,
  "purchase-orders": <><path d="M3 4h3l3 12h9l3-8H7M10 20h.01M18 20h.01" /></>,
  vendors: <><path d="M4 21V7l8-4 8 4v14M8 21v-5h8v5M8 8h.01M16 8h.01M8 12h.01M16 12h.01" /></>,
  ipam: <><rect x="8" y="3" width="8" height="6" rx="1" /><rect x="2" y="16" width="7" height="5" rx="1" /><rect x="15" y="16" width="7" height="5" rx="1" /><path d="M12 9v4M5.5 16v-3h13v3" /></>,
  kb: <><path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1v15" /></>,
  scripts: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18" /></>,
  reports: <><path d="M4 3v18h17M8 17v-5m5 5V7m5 10V4" /></>,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  portal: <><path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6" /></>,
  preferences: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
  logout: <><path d="M9 4H4v16h5m6-13 5 5-5 5m-6-5h11" /></>,
  collapse: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16m7-11-3 3 3 3" /></>,
};
function Icon({ name }: { name: string }) {
  return <svg className="console-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[name] ?? ICONS.tickets}</svg>;
}

/** Shared console chrome only. Collapsing/resizing never changes the mounted workspace. */
export function ConsoleLayout({ me, brand, onLogout, logoutPending, children }: {
  me: Me; brand: Branding; onLogout: () => void; logoutPending: boolean; children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(() => window.matchMedia?.("(max-width: 1050px)").matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 1050px)");
    if (!media) return;
    const resize = (event: MediaQueryListEvent) => setCollapsed(event.matches);
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);
  const pathname = useLocation().pathname;
  const destinations = [{ id: "tickets", label: "Tickets", to: "/" }, ...visibleModules(me)];
  const groups = GROUPS.map(group => ({ ...group, links: group.ids.flatMap(id => destinations.filter(item => item.id === id)) })).filter(group => group.links.length);
  const current = [{ id: "tickets", label: "Tickets", to: "/" }, ...OPTIONAL_MODULES].find(item => item.to === "/" ? pathname === "/" || pathname.startsWith("/tickets/") : pathname === item.to || pathname.startsWith(item.to + "/"));
  const context = pathname.startsWith("/preferences") ? "My preferences" : current?.label ?? "Service desk";
  const group = GROUPS.find(item => item.ids.some(id => id === current?.id))?.label ?? "Workspace";
  const role = me.role === "admin" ? "Administrator" : "Technician";
  return <div className={`shell console-frame${collapsed ? " console-frame-collapsed" : ""}`}>
    <a className="skip-link" href="#console-main">Skip to workspace</a>
    <nav className="side" aria-label="Main">
      <div className="console-brand-row">
        <div className="brand" title={brand.product_name}><Brand b={brand} className="console-brand-content" /></div>
        <button className="console-nav-toggle" type="button" aria-label={collapsed ? "Expand navigation" : "Collapse navigation"} aria-expanded={!collapsed} aria-controls="console-module-navigation" onClick={() => setCollapsed(value => !value)}><Icon name="collapse" /></button>
      </div>
      <div className="console-module-navigation" id="console-module-navigation">
        {groups.map(item => <div className="console-nav-group" role="group" aria-label={item.label} key={item.label}>
          <div className="console-nav-heading" aria-hidden="true">{item.label}</div>
          {item.links.map(link => <Link key={link.id} to={link.to} className={`console-nav-link${current?.id === link.id ? " active" : ""}`} aria-current={current?.id === link.id ? "page" : undefined} aria-label={link.label} title={collapsed ? link.label : undefined}>
            <Icon name={link.id} /><span className="console-nav-label">{link.label}</span>
          </Link>)}
        </div>)}
      </div>
      <div className="me console-account">
        <div className="me-identity"><span className="me-avatar" aria-hidden="true">{me.display_name.trim().slice(0, 1).toUpperCase()}</span><div className="console-nav-label console-account-name"><strong>{me.display_name}</strong><span>{role}</span></div></div>
        <NavLink to="/preferences" aria-label="My preferences" title={collapsed ? "My preferences" : undefined}><Icon name="preferences" /><span className="console-nav-label">My preferences</span></NavLink>
        <button type="button" onClick={onLogout} disabled={logoutPending} aria-label="Sign out" title={collapsed ? "Sign out" : undefined}><Icon name="logout" /><span className="console-nav-label">Sign out</span></button>
        <SourceNotice className="source-notice side-source" compact={!collapsed} stacked={collapsed} />
      </div>
    </nav>
    <main id="console-main" tabIndex={-1} className="main" style={backgroundStyle(brand, "console")}>
      <header className="console-topbar">
        <div className="console-context"><span>{group}</span><strong>{context}</strong></div>
        <div className="console-command-slot"><CommandPalette me={me} /></div>
        <div className="console-header-actions"><UnacceptedCounter /><div className="console-header-identity"><span className="me-avatar" aria-hidden="true">{me.display_name.trim().slice(0, 1).toUpperCase()}</span><span>{me.display_name}<small>{role}</small></span></div></div>
      </header>
      {children}
    </main>
  </div>;
}
