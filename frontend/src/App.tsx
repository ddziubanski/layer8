import { ModuleWorkspace } from "./components/ModuleWorkspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, type ComponentType } from "react";
import { createBrowserRouter, Navigate, NavLink, Outlet, RouterProvider, useParams } from "react-router";

import { ApiError, api, type Me } from "./api/client";
import { Brand, backgroundStyle, useBranding } from "./branding";
import { NoticeBanner, NoticeDialog } from "./components/Notices";
import { ME_KEY } from "./queryClient";
import { RouteFocus } from "./components/RouteFocus";
import { CommandPalette } from "./components/CommandPalette";
import { ChangePasswordFirst, Login, skipSilentWindowsSignIn } from "./pages/Login";
import { QueuePage } from "./pages/QueuePage";
import { TicketPage } from "./pages/TicketPage";

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
const ScriptsPage = named(() => import("./pages/scripts/ScriptsPage"), "ScriptsPage");
const KnowledgePage = named(() => import("./pages/kb/KnowledgePage"), "KnowledgePage");
const PurchaseOrdersPage = named(() => import("./pages/purchasing/PurchaseOrdersPage"), "PurchaseOrdersPage");
const PurchaseOrderPage = named(() => import("./pages/purchasing/PurchaseOrdersPage"), "PurchaseOrderPage");
const ReceivingSheet = named(() => import("./pages/purchasing/Receiving"), "ReceivingSheet");
const ReceivePage = named(() => import("./pages/purchasing/Receiving"), "ReceivePage");
const SurveyPage = named(() => import("./portal/Survey"), "SurveyPage");
const VendorsPage = named(() => import("./pages/vendors/VendorsPage"), "VendorsPage");
const IpamPage = named(() => import("./pages/ipam/IpamPage"), "IpamPage");
const DevicesPage = named(() => import("./pages/devices/DevicesPage"), "DevicesPage");
const DevicePage = named(() => import("./pages/devices/DevicesPage"), "DevicePage");
const BrandingAdmin = named(() => import("./pages/admin/BrandingAdmin"), "BrandingAdmin");
const DirectoryAdmin = named(() => import("./pages/admin/DirectoryAdmin"), "DirectoryAdmin");
const EmailAdmin = named(() => import("./pages/admin/EmailAdmin"), "EmailAdmin");
const Action1Admin = named(() => import("./pages/admin/Action1Admin"), "Action1Admin");
const ApiKeysAdmin = named(() => import("./pages/admin/ApiKeysAdmin"), "ApiKeysAdmin");
const SurveysAdmin = named(() => import("./pages/admin/SurveysAdmin"), "SurveysAdmin");
const PermissionsAdmin = named(() => import("./pages/admin/PermissionsAdmin"), "PermissionsAdmin");
const ChecklistsAdmin = named(() => import("./pages/admin/ChecklistsAdmin"), "ChecklistsAdmin");
const ChangesAdmin = named(() => import("./pages/admin/ChangesAdmin"), "ChangesAdmin");
const ChangesPage = named(() => import("./pages/changes/ChangesPage"), "ChangesPage");
const ChangePage = named(() => import("./pages/changes/ChangesPage"), "ChangePage");
const GroupsAdmin = named(() => import("./pages/admin/GroupsAdmin"), "GroupsAdmin");
const NoticesAdmin = named(() => import("./pages/admin/NoticesAdmin"), "NoticesAdmin");
const QueuesAdmin = named(() => import("./pages/admin/QueuesAdmin"), "QueuesAdmin");
const RulesAdmin = named(() => import("./pages/admin/RulesAdmin"), "RulesAdmin");
const SettingsForm = named(() => import("./pages/admin/SettingsForm"), "SettingsForm");
const OnboardingAdmin = named(() => import("./pages/admin/OnboardingAdmin"), "OnboardingAdmin");
const RunnersAdmin = named(() => import("./pages/admin/RunnersAdmin"), "RunnersAdmin");
const BackupAdmin = named(() => import("./pages/admin/BackupAdmin"), "BackupAdmin");
const StatusAdmin = named(() => import("./pages/admin/StatusAdmin"), "StatusAdmin");
const SystemLogAdmin = named(() => import("./pages/admin/SystemLogAdmin"), "SystemLogAdmin");
const TicketFieldsAdmin = named(() => import("./pages/admin/TicketFieldsAdmin"), "TicketFieldsAdmin");
const UsersAdmin = named(() => import("./pages/admin/UsersAdmin"), "UsersAdmin");
const WindowsSignInAdmin = named(() => import("./pages/admin/WindowsSignInAdmin"), "WindowsSignInAdmin");
import { portalRouter, portalRoutes } from "./portal/PortalApp";
import { can, uses } from "./permissions";


function Shell({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const brand = useBranding();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      skipSilentWindowsSignIn();
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] });
      queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
  });
  return (
    <div className="shell">
      <a className="skip-link" href="#console-main">Skip to workspace</a>
      <nav className="side" aria-label="Main">
        <div className="brand">
          <Brand b={brand} className="" />
        </div>
        <NavLink to="/" end>
          <span className="nav-symbol" aria-hidden="true">▦</span><span>Tickets</span>
        </NavLink>
        <NavLink to="/dashboard"><span className="nav-symbol" aria-hidden="true">◫</span><span>Dashboard</span></NavLink>
        <NavLink to="/devices"><span className="nav-symbol" aria-hidden="true">▣</span><span>Devices</span></NavLink>
        <NavLink to="/changes"><span className="nav-symbol" aria-hidden="true">⇄</span><span>Changes</span></NavLink>
        <NavLink to="/purchase-orders"><span className="nav-symbol" aria-hidden="true">▤</span><span>Purchase orders</span></NavLink>
        <NavLink to="/vendors"><span className="nav-symbol" aria-hidden="true">◈</span><span>Vendors</span></NavLink>
        <NavLink to="/ipam"><span className="nav-symbol" aria-hidden="true">⌗</span><span>IP addresses</span></NavLink>
        <NavLink to="/kb"><span className="nav-symbol" aria-hidden="true">▧</span><span>Knowledge base</span></NavLink>
        <NavLink to="/scripts"><span className="nav-symbol" aria-hidden="true">⌘</span><span>Scripts</span></NavLink>
        <NavLink to="/reports"><span className="nav-symbol" aria-hidden="true">◫</span><span>Reports</span></NavLink>
        <NavLink to="/settings"><span className="nav-symbol" aria-hidden="true">⚙</span><span>Settings</span></NavLink>
        <NavLink to="/portal"><span className="nav-symbol" aria-hidden="true">↗</span><span>Self-service portal</span></NavLink>
        <div className="me">
          <div className="me-identity"><span className="me-avatar" aria-hidden="true">{me.display_name.trim().slice(0, 1).toUpperCase()}</span><span>{me.display_name}</span></div>
          <NavLink to="/preferences">My preferences</NavLink>
          <button type="button" onClick={() => logout.mutate()}>
            Sign out
          </button>
        </div>
      </nav>
      <main id="console-main" tabIndex={-1} className="main" style={backgroundStyle(brand, "console")}>
        <RouteFocus mainId="console-main" />
        <div className="console-topbar"><span className="console-topbar-label">IT OPERATIONS <span aria-hidden="true">/</span> SERVICE DESK</span><CommandPalette me={me} /><span className="console-topbar-user">{me.display_name}</span></div>
        <NoticeBanner />
        <NoticeDialog />
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <ModuleWorkspace><Outlet /></ModuleWorkspace>
        </Suspense>
      </main>
    </div>
  );
}

/** Configuration and setup, one tab per area. Who sees which tab will grow into finer permissions. */
// adminOnly tabs can be handed to others through a permission (Settings > Permissions).
// feature tabs show only while that optional part is in use (Settings > General).
const SETTINGS: { path: string; label: string; adminOnly?: boolean; permission?: string; feature?: string; page: (me: Me) => React.ReactNode }[] = [
  {
    path: "general",
    label: "General",
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
  { path: "branding", label: "Branding", adminOnly: true, page: (me) => <BrandingAdmin me={me} /> },
  { path: "queues", label: "Queues", page: (me) => <QueuesAdmin me={me} /> },
  { path: "rules", label: "Ticket rules", adminOnly: true, permission: "rules_manage", page: (me) => <RulesAdmin me={me} /> },
  { path: "ticket-fields", label: "Ticket fields", adminOnly: true, page: (me) => <TicketFieldsAdmin me={me} /> },
  { path: "users", label: "Users", page: (me) => <UsersAdmin me={me} /> },
  { path: "groups", label: "Groups", page: (me) => <GroupsAdmin me={me} /> },
  { path: "permissions", label: "Permissions", adminOnly: true, page: () => <PermissionsAdmin /> },
  { path: "directory", label: "Directory", adminOnly: true, page: (me) => <DirectoryAdmin me={me} /> },
  { path: "onboarding", label: "Onboarding templates", adminOnly: true, permission: "templates_manage", page: (me) => <OnboardingAdmin me={me} /> },
  { path: "checklists", label: "Checklists", adminOnly: true, permission: "templates_manage", page: (me) => <ChecklistsAdmin me={me} /> },
  { path: "changes", label: "Changes", adminOnly: true, permission: "templates_manage", page: (me) => <ChangesAdmin me={me} /> },
  { path: "email", label: "Email", adminOnly: true, page: (me) => <EmailAdmin me={me} /> },
  { path: "windows-sign-in", label: "Windows sign-in", adminOnly: true, page: (me) => <WindowsSignInAdmin me={me} /> },
  { path: "surveys", label: "Surveys", adminOnly: true, page: (me) => <SurveysAdmin me={me} /> },
  { path: "notices", label: "Notices", adminOnly: true, permission: "notices_manage", page: (me) => <NoticesAdmin me={me} /> },
  { path: "runners", label: "Runners", adminOnly: true, page: (me) => <RunnersAdmin me={me} /> },
  { path: "action1", label: "Action1", adminOnly: true, feature: "action1", page: (me) => <Action1Admin me={me} /> },
  { path: "api-keys", label: "API keys", adminOnly: true, page: (me) => <ApiKeysAdmin me={me} /> },
  {
    path: "sweeps",
    label: "Device sweeps",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="sweeps" title="Device sweeps" intro="When a device counts as stale, and how recent unknown ones must be. The lists are under Devices > Stale and Unknown on the network." />,
  },
  {
    path: "warranty",
    label: "Warranty lookups",
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
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="purchasing" title="Purchasing" intro="Receiving deliveries: the receiving page, who's told when something's on its way and when it arrives. PO numbering is under General." />,
  },
  {
    path: "ipam",
    label: "IP addresses",
    adminOnly: true,
    page: (me) => <SettingsForm me={me} section="ipam" title="IP addresses" intro="Scans: when a documented address counts as gone, and which unknown hosts are flagged. Each subnet's schedule and runner are set on the subnet." />,
  },
  { path: "backup", label: "Backup", adminOnly: true, page: (me) => <BackupAdmin me={me} /> },
  { path: "status", label: "Status", adminOnly: true, page: (me) => <StatusAdmin me={me} /> },
  { path: "log", label: "Log", adminOnly: true, page: (me) => <SystemLogAdmin me={me} /> },
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

const settingsTabs = (me: Me) =>
  SETTINGS.filter((s) => (!s.adminOnly || me.role === "admin" || (s.permission && can(me, s.permission))) && (!s.feature || uses(me, s.feature)));

function SettingsLayout({ me }: { me: Me }) {
  return (
    <>
      <nav className="tabs" aria-label="Settings">
        {settingsTabs(me).map((s) => (
          <NavLink key={s.path} to={`/settings/${s.path}`}>
            {s.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
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
    // Printable, without the console around it.
    {
      path: "purchase-orders/:poId/receiving",
      element: (
        <Suspense fallback={null}>
          <ReceivingSheet />
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
        { path: "purchase-orders", element: <PurchaseOrdersPage /> },
        { path: "purchase-orders/:poId", element: <PurchaseOrderPage me={me} /> },
        { path: "vendors", element: <VendorsPage me={me} /> },
        { path: "vendors/:tab", element: <VendorsPage me={me} /> },
        { path: "changes", element: <ChangesPage /> },
        { path: "changes/:changeId", element: <ChangePage me={me} /> },
        { path: "ipam", element: <IpamPage me={me} /> },
        { path: "ipam/:tab", element: <IpamPage me={me} /> },
        { path: "scripts", element: <ScriptsPage me={me} /> },
        { path: "kb", element: <KnowledgePage me={me} /> },
        { path: "kb/:articleId", element: <KnowledgePage me={me} /> },
        { path: "kb/:articleId/:mode", element: <KnowledgePage me={me} /> },
        { path: "preferences", element: <PreferencesPage me={me} /> },
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
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me });
  // Rebuild the router only when a different user signs in, not on every refetch.
  const userId = me.data?.id;
  // Windows sign-in reaches the portal only, whatever the role.
  const portalOnly = me.data?.portal_only;
  const appRouter = useMemo(
    () => (me.data ? (me.data.role === "requester" || me.data.portal_only ? portalRouter(me.data) : router(me.data)) : null),
    [userId, portalOnly],
  );

  if (me.isPending) return null;
  if (me.error instanceof ApiError && me.error.status === 401) return <Login />;
  if (portalOnly && new URLSearchParams(window.location.search).has("console-sign-in")) return <Login console />;
  if (me.data?.must_change_password) return <ChangePasswordFirst me={me.data} />;
  if (me.error) return <p className="error">Could not reach the server: {me.error.message}</p>;
  return <RouterProvider router={appRouter!} />;
}
