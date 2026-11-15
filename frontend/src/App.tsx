// SPDX-License-Identifier: AGPL-3.0-or-later
import { ModuleWorkspace } from "./components/ModuleWorkspace";
import { ModuleAccess } from "./components/ModuleAccess";
import { ConsoleLayout } from "./components/ConsoleLayout";
import { TeamsCallback } from "./components/TeamsConnection";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState, type ComponentType } from "react";
import { createBrowserRouter, Navigate, NavLink, Outlet, RouterProvider, useLocation, useNavigate, useParams } from "react-router";

import { ApiError, api, type Me } from "./api/client";
import { useBranding } from "./branding";
import { NoticeBanner, NoticeDialog } from "./components/Notices";
import { useServerRecovery, transientFailure } from "./components/ServerRecovery";
import { ME_KEY } from "./queryClient";
import { RouteFocus } from "./components/RouteFocus";
import { ChangePasswordFirst, Login, skipSilentWindowsSignIn } from "./pages/Login";
import { TwoFactorFirst } from "./components/TwoFactor";
import { QueuePage } from "./pages/QueuePage";
import { TicketPage } from "./pages/TicketPage";
import { MobileCompanion } from "./pages/MobileCompanion";

// Reports and Settings load when first opened, so everyone else downloads less.
const named = <K extends string>(load: () => Promise<Record<K, ComponentType<any>>>, name: K) =>
  lazy(() => load().then((m) => ({ default: m[name] }), reloadOnce));

/** A page part that won't load usually means Layer 8 was updated while this tab was open: reload
 * once to get the new version (not again within a minute, so a real outage still shows an error). */
function reloadOnce(error: unknown): never {
  const key = "layer8-reloaded-at";
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(key) ?? 0);
    if (Date.now() - last > 60_000) {
      sessionStorage.setItem(key, String(Date.now()));
      window.location.reload();
    }
  } catch {
    // No session storage: just show the error.
  }
  throw error;
}
const DashboardPage = named(() => import("./pages/DashboardPage"), "DashboardPage");
const ReportsPage = named(() => import("./pages/ReportsPage"), "ReportsPage");
const PreferencesPage = named(() => import("./pages/me/PreferencesPage"), "PreferencesPage");
const ModulesAdmin = named(() => import("./pages/admin/ModulesAdmin"), "ModulesAdmin");
const ScriptsPage = named(() => import("./pages/scripts/ScriptsPage"), "ScriptsPage");
const KnowledgePage = named(() => import("./pages/kb/KnowledgePage"), "KnowledgePage");
const PurchaseOrdersPage = named(() => import("./pages/purchasing/PurchaseOrdersPage"), "PurchaseOrdersPage");
const PurchaseOrderPage = named(() => import("./pages/purchasing/PurchaseOrdersPage"), "PurchaseOrderPage");
const ReceivingSheet = named(() => import("./pages/purchasing/Receiving"), "ReceivingSheet");
const ReceivePage = named(() => import("./pages/purchasing/Receiving"), "ReceivePage");
const SurveyPage = named(() => import("./portal/Survey"), "SurveyPage");
const VendorsPage = named(() => import("./pages/vendors/VendorsPage"), "VendorsPage");
const JunosAdmin = named(() => import("./pages/admin/JunosAdmin"), "JunosAdmin");
const UnifiAdmin = named(() => import("./pages/admin/UnifiAdmin"), "UnifiAdmin");
const NutanixAdmin = named(() => import("./pages/admin/NutanixAdmin"), "NutanixAdmin");
const CannedRepliesAdmin = named(() => import("./pages/admin/CannedRepliesAdmin"), "CannedRepliesAdmin");
const TicketTemplatesAdmin = named(() => import("./pages/admin/TicketTemplatesAdmin"), "TicketTemplatesAdmin");
const MerakiAdmin = named(() => import("./pages/admin/MerakiAdmin"), "MerakiAdmin");
const IpamPage = named(() => import("./pages/ipam/IpamPage"), "IpamPage");
const DevicesPage = named(() => import("./pages/devices/DevicesPage"), "DevicesPage");
const DevicePage = named(() => import("./pages/devices/DevicesPage"), "DevicePage");
const PersonPage = named(() => import("./pages/PersonPage"), "PersonPage");
const BrandingAdmin = named(() => import("./pages/admin/BrandingAdmin"), "BrandingAdmin");
const DirectoryAdmin = named(() => import("./pages/admin/DirectoryAdmin"), "DirectoryAdmin");
const EmailAdmin = named(() => import("./pages/admin/EmailAdmin"), "EmailAdmin");
const RequesterEmailsAdmin = named(() => import("./pages/admin/RequesterEmailsAdmin"), "RequesterEmailsAdmin");
const Action1Admin = named(() => import("./pages/admin/Action1Admin"), "Action1Admin");
const ApiKeysAdmin = named(() => import("./pages/admin/ApiKeysAdmin"), "ApiKeysAdmin");
const WallboardsAdmin = named(() => import("./pages/admin/WallboardsAdmin"), "WallboardsAdmin");
const WallboardPage = named(() => import("./pages/Wallboard"), "WallboardPage");
const SurveysAdmin = named(() => import("./pages/admin/SurveysAdmin"), "SurveysAdmin");
const PermissionsAdmin = named(() => import("./pages/admin/PermissionsAdmin"), "PermissionsAdmin");
const ChecklistsAdmin = named(() => import("./pages/admin/ChecklistsAdmin"), "ChecklistsAdmin");
const ChangesAdmin = named(() => import("./pages/admin/ChangesAdmin"), "ChangesAdmin");
const ChangesPage = named(() => import("./pages/changes/ChangesPage"), "ChangesPage");
const ChangePage = named(() => import("./pages/changes/ChangesPage"), "ChangePage");
const ProblemsPage = named(() => import("./pages/problems/ProblemsPage"), "ProblemsPage");
const GroupsAdmin = named(() => import("./pages/admin/GroupsAdmin"), "GroupsAdmin");
const NoticesAdmin = named(() => import("./pages/admin/NoticesAdmin"), "NoticesAdmin");
const QueuesAdmin = named(() => import("./pages/admin/QueuesAdmin"), "QueuesAdmin");
const RulesAdmin = named(() => import("./pages/admin/RulesAdmin"), "RulesAdmin");
const SettingsForm = named(() => import("./pages/admin/SettingsForm"), "SettingsForm");
const OnboardingAdmin = named(() => import("./pages/admin/OnboardingAdmin"), "OnboardingAdmin");
const RunnersAdmin = named(() => import("./pages/admin/RunnersAdmin"), "RunnersAdmin");
const UpdatesAdmin = named(() => import("./pages/admin/UpdatesAdmin"), "UpdatesAdmin");
const BackupAdmin = named(() => import("./pages/admin/BackupAdmin"), "BackupAdmin");
const ImportsAdmin = named(() => import("./pages/admin/ImportsAdmin"), "ImportsAdmin");
const StatusAdmin = named(() => import("./pages/admin/StatusAdmin"), "StatusAdmin");
const SystemLogAdmin = named(() => import("./pages/admin/SystemLogAdmin"), "SystemLogAdmin");
const TicketFieldsAdmin = named(() => import("./pages/admin/TicketFieldsAdmin"), "TicketFieldsAdmin");
const UsersAdmin = named(() => import("./pages/admin/UsersAdmin"), "UsersAdmin");
const WindowsSignInAdmin = named(() => import("./pages/admin/WindowsSignInAdmin"), "WindowsSignInAdmin");
import { portalRouter, portalRoutes } from "./portal/PortalApp";
import { can, uses } from "./permissions";


function Shell({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  // The router is stable for an account; subscribe to preference saves without rebuilding it.
  const profile = useQuery({ queryKey: ME_KEY, queryFn: api.me, enabled: false });
  const brand = useBranding();
  const activeMe = profile.data?.id === me.id ? profile.data : me;
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      skipSilentWindowsSignIn();
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] });
      queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
  });
  return (
    <ConsoleLayout me={activeMe} brand={brand} onLogout={() => logout.mutate()} logoutPending={logout.isPending}>
        <RouteFocus mainId="console-main" />
        <NoticeBanner />
        <NoticeDialog />
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <ModuleAccess me={activeMe}><ModuleWorkspace><Outlet /></ModuleWorkspace></ModuleAccess>
        </Suspense>
    </ConsoleLayout>
  );
}

/** Configuration and setup, one tab per area. Who sees which tab will grow into finer permissions. */
// adminOnly tabs can be handed to others through a permission (Settings > Permissions).
// feature tabs show only while that optional part is in use (Settings > General).
const SETTINGS: { path: string; label: string; summary: string; adminOnly?: boolean; permission?: string; feature?: string; page: (me: Me) => React.ReactNode }[] = [
  {
    path: "general",
    label: "General",
    summary: "Portal URL, numbering & limits",
    adminOnly: true,
    page: (me) => (
      <SettingsForm
        me={me}
        section="general"
        title="General"
        intro="How people reach the service desk, ticket numbers, and app-wide limits."
        preview={(v: Record<string, unknown>) => <LabelPreview format={String(v.ticket_label_format ?? "")} first={Number(v.ticket_first_number) || 0} />}
      />
    ),
  },
  { path: "modules", label: "Modules", summary: "Enable or hide workspace areas", adminOnly: true, page: (me) => <ModulesAdmin me={me} /> },
  { path: "branding", label: "Branding", summary: "Logo, theme colours & sign-in look", adminOnly: true, page: (me) => <BrandingAdmin me={me} /> },
  { path: "queues", label: "Queues", summary: "Routing, owners & queue hours", page: (me) => <QueuesAdmin me={me} /> },
  { path: "rules", label: "Ticket rules", summary: "Auto-triage, triggers & SQL", adminOnly: true, permission: "rules_manage", page: (me) => <RulesAdmin me={me} /> },
  { path: "ticket-fields", label: "Ticket fields", summary: "Departments, categories & closure", adminOnly: true, page: (me) => <TicketFieldsAdmin me={me} /> },
  { path: "canned-replies", label: "Canned replies", summary: "Reusable technician snippets", page: (me) => <CannedRepliesAdmin me={me} /> },
  { path: "ticket-templates", label: "Ticket templates", summary: "Pre-filled ticket forms", adminOnly: true, permission: "templates_manage", page: (me) => <TicketTemplatesAdmin me={me} /> },
  { path: "users", label: "Users", summary: "Technicians, admins & requesters", page: (me) => <UsersAdmin me={me} /> },
  { path: "groups", label: "Groups", summary: "Local & synced AD groups", page: (me) => <GroupsAdmin me={me} /> },
  { path: "permissions", label: "Permissions", summary: "Delegated technician roles", adminOnly: true, page: () => <PermissionsAdmin /> },
  { path: "directory", label: "Directory (AD)", summary: "LDAPS sync, OUs & account creation", adminOnly: true, page: (me) => <DirectoryAdmin me={me} /> },
  { path: "onboarding", label: "Onboarding templates", summary: "New-hire tasks & AD provisioning", adminOnly: true, permission: "templates_manage", page: (me) => <OnboardingAdmin me={me} /> },
  { path: "checklists", label: "Checklists", summary: "Reusable ticket task lists", adminOnly: true, permission: "templates_manage", page: (me) => <ChecklistsAdmin me={me} /> },
  { path: "changes", label: "Changes", summary: "Approval risk & change templates", adminOnly: true, permission: "templates_manage", page: (me) => <ChangesAdmin me={me} /> },
  { path: "email", label: "Mailbox (Microsoft 365)", summary: "Exchange Online intake & sending", adminOnly: true, page: (me) => <EmailAdmin me={me} /> },
  { path: "requester-emails", label: "Requester emails", summary: "Automated receipt & solved replies", adminOnly: true, page: (me) => <RequesterEmailsAdmin me={me} /> },
  { path: "desk-hours", label: "Desk hours and SLA", summary: "Schedules, holidays & SLA clock", adminOnly: true, page: (me) => <SettingsForm me={me} section="hours" title="Desk hours" intro="When technicians are in. The portal shows each queue as open or closed, with the after-hours message and when someone is next expected. A queue can have its own hours (Settings > Queues). Teams status shows each queue's owners as available, busy, away or offline: status only, with no way to start a chat." /> },
  { path: "library", label: "Library", summary: "SharePoint runbook & FAQ search", adminOnly: true, page: (me) => <SettingsForm me={me} section="library" title="Library" intro="Find SharePoint runbooks and FAQ pages from tickets. This optional connector reuses technician Microsoft connections configured in Settings > Teams. Existing local knowledge-base articles remain available." /> },
  { path: "teams", label: "Teams", summary: "Chat links, Copilot & webhooks", adminOnly: true, page: (me) => <SettingsForm me={me} section="teams" title="Teams" intro="Optional periodic queue counts through a Teams Workflows webhook. Only non-restricted queues contribute. No ticket details or people are sent. Configure a workflow and enable summaries when ready; use Settings > Status to run or pause delivery." /> },
  { path: "windows-sign-in", label: "Windows sign-in", summary: "Portal Kerberos SSO & keytab", adminOnly: true, page: (me) => <WindowsSignInAdmin me={me} /> },
  {
    path: "sign-in",
    label: "Sign-in",
    summary: "2FA policy & Microsoft Entra ID",
    adminOnly: true,
    page: (me) => (
      <div className="stack">
        <nav className="sub-tabs" aria-label="Sign-in sections">
          <NavLink to="/settings/sign-in" end>Two-factor &amp; Microsoft Entra ID</NavLink>
          <NavLink to="/settings/windows-sign-in">Windows sign-in (Kerberos keytab)</NavLink>
          <NavLink to="/settings/branding">Sign-in page appearance</NavLink>
        </nav>
        <SettingsForm me={me} section="signin" title="Sign-in" intro="Two-factor sign-in for admins, and Sign in with Microsoft (Entra ID). Everyone can turn two-factor sign-in on for themselves in My preferences. Recovery from the server: python -m app.cli two-factor-reset <email>. Step by step: docs/entra-sign-in.md." />
      </div>
    ),
  },
  { path: "surveys", label: "Surveys", summary: "Post-resolution CSAT sampling", adminOnly: true, page: (me) => <SurveysAdmin me={me} /> },
  { path: "notices", label: "Notices", summary: "Outage banners & sign-in alerts", adminOnly: true, permission: "notices_manage", page: (me) => <NoticesAdmin me={me} /> },
  { path: "runners", label: "Relays", summary: "LAN pull agents for AD & SNMP", adminOnly: true, page: (me) => <RunnersAdmin me={me} /> },
  { path: "meraki", label: "Meraki", summary: "Clients, switch ports & webhooks", adminOnly: true, page: (me) => <MerakiAdmin me={me} /> },
  { path: "unifi", label: "UniFi", summary: "Network devices & guest vouchers", adminOnly: true, page: (me) => <UnifiAdmin me={me} /> },
  { path: "nutanix", label: "Nutanix", summary: "Prism Central VMs & outages", adminOnly: true, page: (me) => <NutanixAdmin me={me} /> },
  { path: "juniper", label: "Juniper", summary: "Junos switch MAC & ARP reads", adminOnly: true, page: (me) => <JunosAdmin me={me} /> },
  { path: "action1", label: "Action1", summary: "Patching, deploy & remote desktop", adminOnly: true, feature: "action1", page: (me) => <Action1Admin me={me} /> },
  { path: "api-keys", label: "API keys", summary: "Tokens for scripts & integrations", adminOnly: true, page: (me) => <ApiKeysAdmin me={me} /> },
  { path: "wallboards", label: "Remote displays", summary: "Live TV queue & outage boards", adminOnly: true, page: (me) => <WallboardsAdmin me={me} /> },
  {
    path: "sweeps",
    label: "Device sweeps",
    summary: "Stale devices & lifecycle years",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="sweeps" title="Device sweeps" intro="When a device counts as stale, and how recent unknown ones must be. The lists are under Devices > Stale and Unknown on the network." />,
  },
  {
    path: "problems",
    label: "Problems",
    summary: "Shared-symptom clustering rules",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="problems" title="Problems" intro="When open tickets that share a device model, system, switch, subnet, site or a subject word are suggested as one problem (on the Problems page)." />,
  },
  {
    path: "proactive",
    label: "Proactive tickets",
    summary: "Expiring warranties & contracts",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="proactive" title="Proactive tickets" intro="Open one ticket before something lapses: warranties, vendor contracts, service accounts, runners that stop checking in, backups that keep failing. Each ticket shows Verify once its condition is fixed; none closes by itself." />,
  },
  {
    path: "warranty",
    label: "Warranty lookups",
    summary: "Dell TechDirect & Lenovo APIs",
    adminOnly: true,
    page: (me) => (
      <SettingsForm
        me={me}
        section="warranty"
        title="Warranty lookups"
        intro="Dell and Lenovo warranty dates from their own APIs, for devices whose manufacturer is Dell or Lenovo and that have a serial. Empty purchase and warranty dates are filled; a difference is shown on the device to take or leave."
      />
    ),
  },
  {
    path: "purchasing",
    label: "Purchasing",
    summary: "Receiving dock page & alerts",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="purchasing" title="Purchasing" intro="Receiving deliveries: the receiving page, who's told when something's on its way and when it arrives. PO numbering is under General." />,
  },
  {
    path: "ipam",
    label: "IP address scans",
    summary: "DHCP, DNS, SNMP & stale IPs",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="ipam" title="IP addresses" intro="Scans: when a documented address counts as gone, and which unknown hosts are flagged. Each subnet's schedule and relay are set on the subnet." />,
  },
  { path: "updates", label: "Updates", summary: "Signed GitHub release packages", adminOnly: true, page: (me) => <UpdatesAdmin me={me} /> },
  { path: "backup", label: "Backup", summary: "Encrypted archives & verification", adminOnly: true, page: (me) => <BackupAdmin me={me} /> },
  { path: "imports", label: "Imports", summary: "Track-It!, KACE & Spiceworks", adminOnly: true, page: (me) => <ImportsAdmin me={me} /> },
  { path: "status", label: "Status", summary: "Services, jobs & connector health", adminOnly: true, page: (me) => <StatusAdmin me={me} /> },
  { path: "log", label: "Log", summary: "Server & background worker events", adminOnly: true, page: (me) => <SystemLogAdmin me={me} /> },
];

/** How the next ticket's label will look with the format being typed (the server checks it on save). */
function LabelPreview({ format, first }: { format: string; first: number }) {
  const year = new Date().getFullYear();
  const shown = format.replace(/\{(n(?::0?(\d{1,2}))?|yyyy|yy)\}/g, (_, token: string, width?: string) =>
    token === "yyyy" ? String(year) : token === "yy" ? String(year % 100).padStart(2, "0") : width ? String(first).padStart(Number(width), "0") : String(first),
  );
  return (
    <p className="small" role="status" style={{ margin: 0 }}>
      A ticket numbered {first} shows as <strong>{shown}</strong>; email subjects carry <code>[{shown}]</code>.
    </p>
  );
}

// Settings > category > page. A page not listed here shows under "Other" (so a new page is never lost).
const SETTINGS_GROUPS: { name: string; paths: string[] }[] = [
  { name: "Helpdesk", paths: ["queues", "rules", "ticket-fields", "canned-replies", "ticket-templates", "library", "requester-emails", "desk-hours", "surveys", "notices"] },
  { name: "Workflows", paths: ["checklists", "onboarding", "changes", "problems", "purchasing"] },
  { name: "Monitoring", paths: ["meraki", "unifi", "juniper", "nutanix", "ipam", "sweeps", "proactive"] },
  { name: "API / Integrations", paths: ["api-keys", "runners", "email", "teams", "action1", "warranty"] },
  { name: "People and access", paths: ["users", "groups", "permissions", "directory", "sign-in", "windows-sign-in"] },
  { name: "Look and feel", paths: ["branding", "modules", "wallboards"] },
  { name: "System", paths: ["general", "updates", "backup", "imports", "status", "log"] },
];

const allowedSettings = (me: Me) =>
  SETTINGS.filter((s) => (!s.adminOnly || me.role === "admin" || (s.permission && can(me, s.permission))) && (!s.feature || uses(me, s.feature)));

/** The pages this person may open, by category, in the order shown. */
function settingsGroups(me: Me) {
  const pages = allowedSettings(me);
  const listed = new Set(SETTINGS_GROUPS.flatMap((g) => g.paths));
  const groups = SETTINGS_GROUPS.map((g) => ({ name: g.name, pages: g.paths.map((path) => pages.find((p) => p.path === path)).filter((p) => p !== undefined) }));
  groups.push({ name: "Other", pages: pages.filter((p) => !listed.has(p.path)) });
  return groups.filter((g) => g.pages.length > 0);
}

const settingsTabs = (me: Me) => settingsGroups(me).flatMap((g) => g.pages);

const CONNECTOR_CARDS: { key: string; path: string; title: string; subtitle: string; feature?: string }[] = [
  { key: "meraki", path: "meraki", title: "Cisco Meraki", subtitle: "Client observations, switch port diagnostics & outage webhooks" },
  { key: "action1", path: "action1", title: "Action1 Patching & Remote", subtitle: "Endpoint patch status, change windows & remote desktop", feature: "action1" },
  { key: "teams", path: "teams", title: "Microsoft Teams & Copilot", subtitle: "Technician chat links, queue summaries & SharePoint Library" },
  { key: "nutanix", path: "nutanix", title: "Nutanix Prism Central", subtitle: "VM inventory, power actions & critical VM outage tickets" },
  { key: "unifi", path: "unifi", title: "Ubiquiti UniFi", subtitle: "Gateways, switches, APs & guest Wi-Fi hotspot vouchers" },
  { key: "junos", path: "juniper", title: "Juniper Junos Switches", subtitle: "Switch MAC & ARP tables via LAN relay REST API" },
  { key: "email", path: "email", title: "Microsoft 365 Mailbox", subtitle: "Shared mailbox intake, sending & automated requester templates" },
  { key: "directory", path: "directory", title: "Active Directory (LDAPS)", subtitle: "Directory sync, group role mapping & ticket AD actions" },
];

const HUB_CHIP: Record<string, [string, string]> = {
  ok: ["g", "Connected"],
  warning: ["a", "Needs attention"],
  error: ["r", "Problem"],
  off: ["n", "Off"],
  unknown: ["n", "Configured"],
};

function ConnectorsHub({ me, onOpen }: { me: Me; onOpen: (path: string) => void }) {
  const health = useQuery({ queryKey: ["integration-health"], queryFn: api.system.integrations, enabled: me.role === "admin", staleTime: 30_000 });
  const byKey = new Map((health.data?.items ?? []).map((item) => [item.key, item]));
  const cards = CONNECTOR_CARDS.filter((c) => !c.feature || uses(me, c.feature));
  return (
    <section className="stack" aria-label="Connectors hub">
      <div className="page-head">
        <div>
          <h1>Connectors &amp; network integrations</h1>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            Configure active and optional directory, mail, patching, and network integrations from one hub.
          </p>
        </div>
      </div>
      <div className="connector-grid">
        {cards.map((card) => {
          const item = byKey.get(card.key);
          const state = item?.state ?? "unknown";
          const [cls, badge] = HUB_CHIP[state] ?? HUB_CHIP.unknown;
          const active = item?.enabled;
          return (
            <div key={card.key} className={`connector-card${active ? " active-card" : ""}`}>
              <div className="connector-top">
                <div>
                  <h2>{card.title}</h2>
                  <div className="muted small">{card.subtitle}</div>
                </div>
                <span className={`chip ${cls}`}>{badge}</span>
              </div>
              <div className="connector-meta">
                <span>{item?.summary ?? "Open to review or configure connection settings."}</span>
                <button className="btn2" type="button" onClick={() => onOpen(card.path)}>
                  {active ? "Configure" : "Set up"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Settings' own menu: horizontal category pills, a scoped left rail for the active category, and a filter box across all categories. */
function SettingsNavigation({ me, hubOpen, onToggleHub }: { me: Me; hubOpen: boolean; onToggleHub: (open: boolean) => void }) {
  const [find, setFind] = useState("");
  const [picked, setPicked] = useState<{ from?: string; category: string } | null>(null);
  const navigate = useNavigate();
  const currentPath = useLocation().pathname.split("/")[2];
  const words = find.trim().toLowerCase();
  const allGroups = settingsGroups(me);
  const routeCategory = allGroups.find((g) => g.pages.some((p) => p.path === currentPath));
  const activeCategory =
    (picked && picked.from === currentPath ? allGroups.find((g) => g.name === picked.category) : undefined) ??
    routeCategory ??
    allGroups[0];
  const groups = (
    words
      ? allGroups.map((g) => ({
          ...g,
          pages: g.name.toLowerCase().includes(words) ? g.pages : g.pages.filter((p) => `${p.label} ${p.summary}`.toLowerCase().includes(words)),
        }))
      : activeCategory
        ? [activeCategory]
        : []
  ).filter((g) => g.pages.length > 0);
  return (
    <nav className="settings-navigation" aria-label="Settings">
      <div className="setup-category-bar">
        <div className="setup-category-pills" role="tablist" aria-label="Setup categories">
          {allGroups.map((g) => {
            const selected = !words && !hubOpen && activeCategory?.name === g.name;
            return (
              <button
                key={g.name}
                type="button"
                role="tab"
                aria-selected={selected}
                className={selected ? "active" : undefined}
                onClick={() => {
                  setFind("");
                  onToggleHub(false);
                  setPicked({ from: currentPath, category: g.name });
                  navigate(`/settings/${g.pages[0].path}`);
                }}
              >
                {g.name} <span className="pill-count" aria-hidden="true">{g.pages.length}</span>
              </button>
            );
          })}
          {me.role === "admin" && (
            <button
              type="button"
              className={hubOpen ? "active" : undefined}
              aria-pressed={hubOpen}
              onClick={() => onToggleHub(!hubOpen)}
            >
              Connectors hub
            </button>
          )}
        </div>
        <div className="setup-find-wrap">
          <input
            type="search"
            className="settings-find"
            aria-label="Find a setting"
            placeholder="Find a setting"
            value={find}
            onChange={(e) => {
              setFind(e.target.value);
              if (e.target.value) onToggleHub(false);
            }}
          />
        </div>
      </div>
      <div className="settings-rail">
        {groups.map((g) => (
          <div key={g.name} className="settings-group" role="group" aria-label={g.name}>
            <div className="settings-group-name" aria-hidden="true">{g.name}</div>
            {g.pages.map((s) => (
              <NavLink
                key={s.path}
                to={`/settings/${s.path}`}
                data-sub={s.summary}
                onClick={() => {
                  setPicked(null);
                  onToggleHub(false);
                }}
              >
                {s.label}
              </NavLink>
            ))}
          </div>
        ))}
        {groups.length === 0 && <p className="muted small">No setting called that.</p>}
      </div>
    </nav>
  );
}

function SettingsLayout({ me }: { me: Me }) {
  const [hubOpen, setHubOpen] = useState(false);
  const navigate = useNavigate();
  return (
    <section className="settings-workspace">
      <div className="settings-grid">
      <SettingsNavigation me={me} hubOpen={hubOpen} onToggleHub={setHubOpen} />
      <div className="settings-content">
        {hubOpen ? (
          <ConnectorsHub
            me={me}
            onOpen={(path) => {
              setHubOpen(false);
              navigate(`/settings/${path}`);
            }}
          />
        ) : (
          <Suspense fallback={<p className="muted">Loading…</p>}><Outlet /></Suspense>
        )}
      </div>
      </div>
    </section>
  );
}

/** Old /admin/... links (bookmarks) open the same page under Settings. */
function OldAdminLink() {
  const { page } = useParams();
  return <Navigate to={`/settings/${page}`} replace />;
}

const router = (me: Me) =>
  createBrowserRouter([
    // Agents and admins can use the portal as themselves, e.g. to request something.
    portalRoutes(me, "/portal"),
    { path: "mobile", element: <MobileCompanion me={me} /> },
    { path: "mobile/tickets/:ticketId", element: <MobileCompanion me={me} /> },
    // Printable, without the console around it.
    {
      path: "purchase-orders/:poId/receiving",
      element: (
        <Suspense fallback={null}>
          <ModuleAccess me={me}><ReceivingSheet /></ModuleAccess>
        </Suspense>
      ),
    },
    {
      element: <Shell me={me} />,
      children: [
        { index: true, element: <QueuePage me={me} /> },
        { path: "dashboard", element: <DashboardPage /> },
        { path: "tickets/:ticketId", element: <TicketPage me={me} /> },
        { path: "devices", element: <DevicesPage me={me} /> },
        { path: "devices/:deviceId", element: <DevicePage me={me} /> },
        { path: "people/:personId", element: <PersonPage /> },
        { path: "purchase-orders", element: <PurchaseOrdersPage /> },
        { path: "purchase-orders/:poId", element: <PurchaseOrderPage me={me} /> },
        { path: "vendors", element: <VendorsPage me={me} /> },
        { path: "vendors/:tab", element: <VendorsPage me={me} /> },
        { path: "changes", element: <ChangesPage /> },
        { path: "changes/:changeId", element: <ChangePage me={me} /> },
        { path: "problems", element: <ProblemsPage /> },
        { path: "problems/:problemId", element: <ProblemsPage /> },
        { path: "ipam", element: <IpamPage me={me} /> },
        { path: "ipam/:tab", element: <IpamPage me={me} /> },
        { path: "scripts", element: <ScriptsPage me={me} /> },
        { path: "kb", element: <KnowledgePage me={me} /> },
        { path: "kb/:articleId", element: <KnowledgePage me={me} /> },
        { path: "kb/:articleId/:mode", element: <KnowledgePage me={me} /> },
        { path: "preferences", element: <PreferencesPage me={me} /> },
        { path: "teams/callback", element: <TeamsCallback /> },
        { path: "reports", element: <ReportsPage /> },
        {
          path: "settings",
          element: <SettingsLayout me={me} />,
          children: [
            { index: true, element: <Navigate to={settingsTabs(me)[0].path} replace /> },
            ...SETTINGS.map((s) => ({ path: s.path, element: s.page(me) })),
          ],
        },
        { path: "admin/:page", element: <OldAdminLink /> },
        { path: "*", element: <p>Page not found.</p> },
      ],
    },
  ]);

export function App() {
  // The receiving page works without signing in (the PO's code is the key).
  if (window.location.pathname === "/receive") {
    return (
      <Suspense fallback={null}>
        <ReceivePage />
      </Suspense>
    );
  }
  // So does a wallboard (its link, after the #, is the key).
  if (window.location.pathname === "/wallboard") {
    return (
      <Suspense fallback={null}>
        <WallboardPage />
      </Suspense>
    );
  }
  // So does a satisfaction survey's link (the link is the key).
  if (/\/survey\/[^/]+\/?$/.test(window.location.pathname)) {
    return (
      <Suspense fallback={null}>
        <SurveyPage />
      </Suspense>
    );
  }
  return <SignedInApp />;
}

function SignedInApp() {
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, refetchInterval: query => transientFailure(query.state.error) ? 5000 : query.state.data && !query.state.error ? 30000 : false, refetchIntervalInBackground: true });
  // Rebuild the router only when a different user signs in, not on every refetch.
  const userId = me.data?.id;
  const role = me.data?.role;
  const recovery = useServerRecovery(me.error, me.data, () => me.refetch());
  // Windows sign-in reaches the portal only, whatever the role.
  const portalOnly = me.data?.portal_only;
  const appRouter = useMemo(
    () => (me.data ? (me.data.role === "requester" || me.data.portal_only ? portalRouter(me.data) : router(me.data)) : null),
    [userId, role, portalOnly],
  );

  let content;
  if (me.isPending) content = null;
  else if (me.error instanceof ApiError && me.error.status === 401) content = <Login />;
  else if (portalOnly && new URLSearchParams(window.location.search).has("console-sign-in")) content = <Login console />;
  else if (me.data?.must_change_password) content = <ChangePasswordFirst me={me.data} />;
  else if (me.data?.must_set_up_two_factor) content = <TwoFactorFirst me={me.data} />;
  else if (me.error && !transientFailure(me.error)) content = <p className="error">Could not reach the server: {me.error.message} <button onClick={() => me.refetch()}>Try again</button></p>;
  else content = appRouter ? <RouterProvider router={appRouter} /> : null;
  return <>{recovery.banner}<div className="connection-workspace" inert={recovery.blocked || undefined}>{content}</div></>;
}
