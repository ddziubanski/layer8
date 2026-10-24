// SPDX-License-Identifier: AGPL-3.0-or-later
import { ModuleWorkspace } from "./components/ModuleWorkspace";
import { ModuleAccess } from "./components/ModuleAccess";
import { ConsoleLayout } from "./components/ConsoleLayout";
import { TeamsCallback } from "./components/TeamsConnection";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, type ComponentType } from "react";
import { createBrowserRouter, Navigate, NavLink, Outlet, RouterProvider, useParams } from "react-router";

import { ApiError, api, type Me } from "./api/client";
import { useBranding } from "./branding";
import { NoticeBanner, NoticeDialog } from "./components/Notices";
import { ME_KEY } from "./queryClient";
import { RouteFocus } from "./components/RouteFocus";
import { ChangePasswordFirst, Login, skipSilentWindowsSignIn } from "./pages/Login";
import { TwoFactorFirst } from "./components/TwoFactor";
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
const MerakiAdmin = named(() => import("./pages/admin/MerakiAdmin"), "MerakiAdmin");
const IpamPage = named(() => import("./pages/ipam/IpamPage"), "IpamPage");
const DevicesPage = named(() => import("./pages/devices/DevicesPage"), "DevicesPage");
const DevicePage = named(() => import("./pages/devices/DevicesPage"), "DevicePage");
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
const GroupsAdmin = named(() => import("./pages/admin/GroupsAdmin"), "GroupsAdmin");
const NoticesAdmin = named(() => import("./pages/admin/NoticesAdmin"), "NoticesAdmin");
const QueuesAdmin = named(() => import("./pages/admin/QueuesAdmin"), "QueuesAdmin");
const RulesAdmin = named(() => import("./pages/admin/RulesAdmin"), "RulesAdmin");
const SettingsForm = named(() => import("./pages/admin/SettingsForm"), "SettingsForm");
const OnboardingAdmin = named(() => import("./pages/admin/OnboardingAdmin"), "OnboardingAdmin");
const RunnersAdmin = named(() => import("./pages/admin/RunnersAdmin"), "RunnersAdmin");
const UpdatesAdmin = named(() => import("./pages/admin/UpdatesAdmin"), "UpdatesAdmin");
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
  { path: "modules", label: "Modules", adminOnly: true, page: (me) => <ModulesAdmin me={me} /> },
  { path: "branding", label: "Branding", adminOnly: true, page: (me) => <BrandingAdmin me={me} /> },
  { path: "queues", label: "Queues", page: (me) => <QueuesAdmin me={me} /> },
  { path: "rules", label: "Ticket rules", adminOnly: true, permission: "rules_manage", page: (me) => <RulesAdmin me={me} /> },
  { path: "ticket-fields", label: "Ticket fields", adminOnly: true, page: (me) => <TicketFieldsAdmin me={me} /> },
  { path: "canned-replies", label: "Canned replies", page: (me) => <CannedRepliesAdmin me={me} /> },
  { path: "users", label: "Users", page: (me) => <UsersAdmin me={me} /> },
  { path: "groups", label: "Groups", page: (me) => <GroupsAdmin me={me} /> },
  { path: "permissions", label: "Permissions", adminOnly: true, page: () => <PermissionsAdmin /> },
  { path: "directory", label: "Directory", adminOnly: true, page: (me) => <DirectoryAdmin me={me} /> },
  { path: "onboarding", label: "Onboarding templates", adminOnly: true, permission: "templates_manage", page: (me) => <OnboardingAdmin me={me} /> },
  { path: "checklists", label: "Checklists", adminOnly: true, permission: "templates_manage", page: (me) => <ChecklistsAdmin me={me} /> },
  { path: "changes", label: "Changes", adminOnly: true, permission: "templates_manage", page: (me) => <ChangesAdmin me={me} /> },
  { path: "email", label: "Email", adminOnly: true, page: (me) => <EmailAdmin me={me} /> },
  { path: "requester-emails", label: "Requester emails", adminOnly: true, page: (me) => <RequesterEmailsAdmin me={me} /> },
  { path: "desk-hours", label: "Desk hours", adminOnly: true, page: (me) => <SettingsForm me={me} section="hours" title="Desk hours" intro="When technicians are in. The portal shows each queue as open or closed, with the after-hours message and when someone is next expected. A queue can have its own hours (Settings > Queues). Teams status shows each queue's owners as available, busy, away or offline: status only, with no way to start a chat." /> },
  { path: "teams", label: "Teams", adminOnly: true, page: (me) => <SettingsForm me={me} section="teams" title="Teams" intro="Optional periodic queue counts through a Teams Workflows webhook. Only non-restricted queues contribute. No ticket details or people are sent. Configure a workflow and enable summaries when ready; use Settings > Status to run or pause delivery." /> },
  { path: "windows-sign-in", label: "Windows sign-in", adminOnly: true, page: (me) => <WindowsSignInAdmin me={me} /> },
  { path: "sign-in", label: "Sign-in", adminOnly: true, page: (me) => <SettingsForm me={me} section="signin" title="Sign-in" intro="Two-factor sign-in for admins, and Sign in with Microsoft (Entra ID). Everyone can turn two-factor sign-in on for themselves in My preferences. Recovery from the server: python -m app.cli two-factor-reset <email>. Step by step: docs/entra-sign-in.md." /> },
  { path: "surveys", label: "Surveys", adminOnly: true, page: (me) => <SurveysAdmin me={me} /> },
  { path: "notices", label: "Notices", adminOnly: true, permission: "notices_manage", page: (me) => <NoticesAdmin me={me} /> },
  { path: "runners", label: "Relays", adminOnly: true, page: (me) => <RunnersAdmin me={me} /> },
  { path: "meraki", label: "Meraki", adminOnly: true, page: (me) => <MerakiAdmin me={me} /> },
  { path: "unifi", label: "UniFi", adminOnly: true, page: (me) => <UnifiAdmin me={me} /> },
  { path: "nutanix", label: "Nutanix", adminOnly: true, page: (me) => <NutanixAdmin me={me} /> },
  { path: "juniper", label: "Juniper", adminOnly: true, page: (me) => <JunosAdmin me={me} /> },
  { path: "action1", label: "Action1", adminOnly: true, feature: "action1", page: (me) => <Action1Admin me={me} /> },
  { path: "api-keys", label: "API keys", adminOnly: true, page: (me) => <ApiKeysAdmin me={me} /> },
  { path: "wallboards", label: "Remote displays", adminOnly: true, page: (me) => <WallboardsAdmin me={me} /> },
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
    page: (me) => <SettingsForm me={me} section="ipam" title="IP addresses" intro="Scans: when a documented address counts as gone, and which unknown hosts are flagged. Each subnet's schedule and relay are set on the subnet." />,
  },
  { path: "updates", label: "Updates", adminOnly: true, page: (me) => <UpdatesAdmin me={me} /> },
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
    <section className="settings-workspace">
      <div className="settings-grid">
      <nav className="tabs settings-navigation" aria-label="Settings">
        {settingsTabs(me).map((s) => (
          <NavLink key={s.path} to={`/settings/${s.path}`}>
            {s.label}
          </NavLink>
        ))}
      </nav>
      <div className="settings-content"><Outlet /></div>
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
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, refetchInterval: query => query.state.data && !query.state.error ? 30000 : false });
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
  if (me.data?.must_set_up_two_factor) return <TwoFactorFirst me={me.data} />;
  if (me.error) return <p className="error">Could not reach the server: {me.error.message}</p>;
  return <RouterProvider router={appRouter!} />;
}
