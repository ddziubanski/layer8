// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import type { Branding, Me } from "../api/client";
import { Brand } from "../branding";
import { OPTIONAL_MODULES, visibleModules } from "../navigation";
import { CommandPalette } from "./CommandPalette";
import { consoleAppearance, materialTokens } from "../materialThemes";
import { SourceNotice } from "./SourceNotice";
import { UnacceptedCounter } from "./UnacceptedCounter";

const GROUPS = [
  { label: "Helpdesk", ids: ["tickets", "dashboard", "changes"] },
  { label: "People & devices", ids: ["devices"] },
  { label: "Operations", ids: ["ipam", "scripts", "purchase-orders", "vendors", "reports"] },
  { label: "Library", ids: ["kb"] },
  { label: "Setup", ids: ["settings"] },
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

/** Ticket Focus chrome: five workspaces, with local destinations below the header. */
export function ConsoleLayout({ me, brand, onLogout, logoutPending, children }: {
  me: Me; brand: Branding; onLogout: () => void; logoutPending: boolean; children: ReactNode;
}) {
  const [prefersDark, setPrefersDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const change = (event: MediaQueryListEvent) => setPrefersDark(event.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  const pathname = useLocation().pathname;
  const destinations = [{ id: "tickets", label: "Tickets", to: "/" }, ...visibleModules(me)];
  const groups = GROUPS.map(group => ({ ...group, links: group.ids.flatMap(id => destinations.filter(item => item.id === id)) })).filter(group => group.links.length);
  const current = [{ id: "tickets", label: "Tickets", to: "/" }, ...OPTIONAL_MODULES].find(item => item.to === "/" ? pathname === "/" || pathname.startsWith("/tickets/") : pathname === item.to || pathname.startsWith(item.to + "/"));
  const activeGroup = groups.find(group => group.ids.includes(current?.id ?? ""));
  const appearance = consoleAppearance(me, brand, prefersDark);
  const role = me.role === "admin" ? "Administrator" : "Technician";
  const portal = destinations.find(item => item.id === "portal");
  return <div className="material-console" data-appearance={appearance.dark ? "dark" : "light"} data-palette={appearance.palette} style={materialTokens(appearance.palette, appearance.dark)}>
    <a className="skip-link" href="#console-main">Skip to workspace</a>
    <header className="material-topbar">
      <Link to="/" className="material-brand"><Brand b={brand} /></Link>
      <div className="console-command-slot"><CommandPalette me={me} /></div>
      <div className="material-account"><UnacceptedCounter /><NavLink to="/preferences" aria-label="My preferences"><span className="me-avatar" aria-hidden="true">{me.display_name.trim().slice(0, 1).toUpperCase()}</span><span>{me.display_name}<small>{role}</small></span></NavLink><button className="btn2" type="button" onClick={onLogout} disabled={logoutPending}>Sign out</button></div>
    </header>
    <nav className="material-workspaces" aria-label="Main">
      {groups.map(group => <Link key={group.label} to={group.links[0].to} className={activeGroup?.label === group.label ? "active" : undefined} aria-current={activeGroup?.label === group.label ? "true" : undefined}><Icon name={group.links[0].id} />{group.label}</Link>)}
      {portal && <Link to={portal.to} className="material-portal">{portal.label}<Icon name="portal" /></Link>}
    </nav>
    <main id="console-main" tabIndex={-1} className="material-main">
      <div className="material-workspace-header"><div><span className="workspace-area">YOUR WORKSPACE</span><h1>{pathname.startsWith("/preferences") ? "My preferences" : activeGroup?.label ?? "Helpdesk"}</h1></div><span className="small muted">{brand.product_name}</span></div>
      {activeGroup && <nav className="material-local-nav" aria-label="Workspace">{activeGroup.links.map(link => <Link key={link.id} to={link.to} className={current?.id === link.id ? "active" : undefined} aria-current={current?.id === link.id ? "page" : undefined}>{link.label}</Link>)}</nav>}
      {children}
    </main>
    <footer className="material-source"><SourceNotice /></footer>
  </div>;
}
