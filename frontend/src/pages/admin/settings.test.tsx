// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import { createQueryClient } from "../../queryClient";
import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { BackupAdmin } from "./BackupAdmin";
import { RunnersAdmin } from "./RunnersAdmin";
import { BrandingAdmin } from "./BrandingAdmin";
import { NoticeBanner, NoticeDialog } from "../../components/Notices";
import { EmailAdmin } from "./EmailAdmin";
import { NoticesAdmin } from "./NoticesAdmin";
import { StatusAdmin } from "./StatusAdmin";
import { SystemLogAdmin } from "./SystemLogAdmin";
import { TicketFieldsAdmin } from "./TicketFieldsAdmin";
import { SettingsForm } from "./SettingsForm";
import { WindowsSignInAdmin } from "./WindowsSignInAdmin";

const ADMIN = { ...ME, role: "admin" as const };
const EMPTY = {
  enabled: false,
  silent: true,
  realm: "",
  keytab_principals: [],
  keytab_enctypes: [],
  keytab_uploaded_at: null,
  keytab_uploaded_by: null,
  problem: null,
};
const UPLOADED = {
  ...EMPTY,
  realm: "CORP.EXAMPLE",
  keytab_principals: ["HTTP/helpdesk.corp.example@CORP.EXAMPLE"],
  keytab_enctypes: ["aes256-cts-hmac-sha1-96"],
  keytab_uploaded_at: new Date().toISOString(),
  keytab_uploaded_by: "D. Admin",
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function mountApp(path: string) {
  window.history.replaceState(null, "", path);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <App />
    </QueryClientProvider>,
  );
}

test("configuration lives under Settings, with tabs by role", async () => {
  mockApi({ "GET /api/auth/me": ADMIN, "GET /api/settings/windows-sign-in": EMPTY });
  const view = mountApp("/settings/windows-sign-in");
  expect(await screen.findByRole("heading", { name: "Windows sign-in" })).toBeInTheDocument();
  const tabs = screen.getByRole("navigation", { name: "Settings" });
  expect([...tabs.querySelectorAll("a")].map((a) => a.textContent)).toEqual([
    "Queues", "Ticket rules", "Ticket fields", "Canned replies", "Ticket templates", "Library", "Requester emails", "Desk hours and SLA", "Surveys", "Notices",
    "Checklists", "Onboarding templates", "Changes", "Purchasing",
    "Meraki", "UniFi", "Juniper", "Nutanix", "IP address scans", "Device sweeps",
    "API keys", "Relays", "Mailbox (Microsoft 365)", "Teams", "Action1", "Warranty lookups",
    "Users", "Groups", "Permissions", "Directory (AD)", "Sign-in", "Windows sign-in",
    "Branding", "Modules", "Remote displays",
    "General", "Updates", "Backup", "Imports", "Status", "Log",
  ]);
  expect([...tabs.querySelectorAll("[role=group]")].map((g) => g.getAttribute("aria-label"))).toEqual(
    ["Helpdesk", "Workflows", "Monitoring", "API / Integrations", "People and access", "Look and feel", "System"]);
  // Finding a page narrows the menu; a category's name keeps its pages.
  await userEvent.type(within(tabs).getByLabelText("Find a setting"), "uni");
  expect([...tabs.querySelectorAll("a")].map((a) => a.textContent)).toEqual(["UniFi", "Juniper"]);
  await userEvent.clear(within(tabs).getByLabelText("Find a setting"));
  await userEvent.type(within(tabs).getByLabelText("Find a setting"), "monitoring");
  expect(tabs.querySelectorAll("a")).toHaveLength(6);
  expect(screen.getByRole("link", { name: "Settings" })).toHaveClass("active");
  view.unmount();

  mockApi({ "GET /api/auth/me": ME });
  mountApp("/admin/groups"); // an old bookmark
  await waitFor(() => expect(window.location.pathname).toBe("/settings/groups"));
  const agentTabs = await screen.findByRole("navigation", { name: "Settings" });
  expect([...agentTabs.querySelectorAll("a")].map((a) => a.textContent)).toEqual(["Queues", "Canned replies", "Users", "Groups"]);
});

test("upload a keytab, then turn Windows sign-in on", async () => {
  const calls = mockApi({
    "GET /api/settings/windows-sign-in": EMPTY,
    "PUT /api/settings/windows-sign-in/keytab": UPLOADED,
    "PUT /api/settings/windows-sign-in": ({ body }: Call) => ({ ...UPLOADED, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <WindowsSignInAdmin me={ADMIN} /> }]);
  const offer = await screen.findByLabelText(/Offer “Sign in with your Windows account”/);
  expect(offer).toBeDisabled();

  await userEvent.upload(screen.getByLabelText("Upload keytab"), new File([new Uint8Array([5, 2])], "http.keytab"));
  expect(await screen.findByText("HTTP/helpdesk.corp.example@CORP.EXAMPLE")).toBeInTheDocument();
  expect(screen.getByText("CORP.EXAMPLE")).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/settings/windows-sign-in/keytab")?.body).toBeInstanceOf(FormData);

  await userEvent.click(screen.getByLabelText(/Offer “Sign in with your Windows account”/));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.path === "/api/settings/windows-sign-in")?.body).toEqual({ enabled: true, silent: true }));
  expect(await screen.findByLabelText(/Offer/)).toBeChecked();
});

test("a refused keytab says why", async () => {
  mockApi({
    "GET /api/settings/windows-sign-in": EMPTY,
    "PUT /api/settings/windows-sign-in/keytab": () =>
      new Response(JSON.stringify({ detail: "The keytab only has RC4 or DES keys, which current Kerberos refuses." }), { status: 422 }),
  });
  renderAt("/", [{ path: "/", element: <WindowsSignInAdmin me={ADMIN} /> }]);
  await userEvent.upload(await screen.findByLabelText("Upload keytab"), new File([new Uint8Array([5, 2])], "old.keytab"));
  expect(await screen.findByRole("alert")).toHaveTextContent("RC4");
});

test("general settings: only changed fields are saved; server-set fields are read-only", async () => {
  const field = (name: string, label: string, kind: string, value: unknown, locked = false) => ({ name, label, help: "", kind, value, locked, is_set: null });
  const GENERAL = {
    section: "general",
    fields: [
      field("portal_url", "Portal address", "text", ""),
      field("session_hours", "Sign-in lasts (hours)", "int", 12, true),
      field("max_upload_mb", "Largest attachment (MB)", "int", 25),
    ],
  };
  const calls = mockApi({
    "GET /api/settings/general": GENERAL,
    "PUT /api/settings/general": { ...GENERAL, fields: [field("portal_url", "Portal address", "text", "https://helpdesk.corp.example"), ...GENERAL.fields.slice(1)] },
  });
  renderAt("/", [{ path: "/", element: <SettingsForm me={ADMIN} section="general" title="General" /> }]);
  expect(await screen.findByLabelText(/Sign-in lasts/)).toBeDisabled();
  expect(screen.getByText(/HELPDESK_SESSION_HOURS/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

  await userEvent.type(screen.getByLabelText(/Portal address/), "https://helpdesk.corp.example");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { portal_url: "https://helpdesk.corp.example" }, clear: [] }));
  expect(await screen.findByText("Saved.")).toBeInTheDocument();
});

test("email: save the settings, then test the connection", async () => {
  const field = (name: string, label: string, kind: string, value: unknown, is_set: boolean | null = null) => ({ name, label, help: "", kind, value, locked: false, choices: [], is_set });
  const EMAIL = {
    section: "email",
    fields: [
      field("mailbox", "Service desk mailbox", "text", "helpdesk@example.com"),
      field("graph_client_secret", "Client secret", "secret", null, true),
      field("mail_allowed_domains", "Accept email from", "list", ["example.com"]),
    ],
  };
  const calls = mockApi({
    "GET /api/settings/email": EMAIL,
    "PUT /api/settings/email": EMAIL,
    "POST /api/settings/email/test": () =>
      new Response(JSON.stringify({ detail: "Graph sign-in failed: invalid_client: AADSTS7000215: Invalid client secret provided." }), { status: 502 }),
  });
  renderAt("/", [{ path: "/", element: <EmailAdmin me={ADMIN} /> }]);
  const domains = await screen.findByLabelText("Accept email from");
  expect(domains).toHaveValue("example.com");
  await userEvent.type(domains, "\ncorp.example");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { mail_allowed_domains: ["example.com", "corp.example"] }, clear: [] }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("AADSTS7000215");
});

test("check the mailbox now, and see what happened to each message", async () => {
  mockApi({
    "GET /api/settings/email": { section: "email", fields: [] },
    "POST /api/settings/email/check-now": {
      processed: 1,
      rejected: 1,
      failed: 0,
      sent: 1,
      items: [
        { status: "processed", ticket_id: 4821, reason: null },
        { status: "rejected", ticket_id: null, reason: "sender's domain isn't accepted" },
      ],
    },
  });
  renderAt("/", [{ path: "/", element: <EmailAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Check mailbox now" }));
  expect(await screen.findByText(/1 made into tickets or replies, 1 rejected, 0 failed; 1 email\(s\) sent/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "#4821" })).toHaveAttribute("href", "/tickets/4821");
  expect(screen.getByText(/Rejected: sender's domain isn't accepted/)).toBeInTheDocument();
});

test("log tab: the log with filters and details", async () => {
  const at = new Date().toISOString();
  const calls = mockApi({
    "GET /api/system/log/sources": [
      { value: "directory", label: "Directory" },
      { value: "email", label: "Email" },
    ],
    "GET /api/system/log": [
      { id: 2, at, level: "error", source: "directory", message: "Directory sync failed", details: "Traceback ...\nLDAPSocketOpenError", process: "worker" },
      { id: 1, at, level: "info", source: "email", message: "Email connection test: helpdesk@example.com OK", details: null, process: "api" },
    ],
  });
  renderAt("/", [{ path: "/", element: <SystemLogAdmin me={ADMIN} /> }]);
  expect(await screen.findByText("Directory sync failed")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText(/LDAPSocketOpenError/)).toBeInTheDocument();

  await userEvent.selectOptions(screen.getByLabelText("Show"), "error");
  await userEvent.selectOptions(screen.getByLabelText("Source"), "directory");
  await waitFor(() => expect(calls.some((c) => c.path.includes("source=directory") && c.path.includes("level=error"))).toBe(true));
});

const at = () => new Date().toISOString();
const job = (name: string, label: string, over = {}) => ({
  name,
  label,
  paused: false,
  running: false,
  run_requested: false,
  last_started_at: at(),
  last_finished_at: at(),
  last_ok: true,
  last_summary: "Inbox: nothing new; 0 sent",
  last_error: null,
  next_due_at: at(),
  ...over,
});
const SERVICES = {
  api: { host: "helpdesk", pid: 101, started_at: at(), version: "0.1.0" },
  worker: { state: "running", last_seen: at(), host: "helpdesk", pid: 202, started_at: at(), restart_requested_at: null, restart_requested_by: null, can_force_restart: false },
  jobs: [
    job("email", "Email: check the mailbox and send"),
    job("directory", "Directory sync", { last_ok: false, last_error: "RuntimeError: Can't contact LDAP server" }),
  ],
};

test("status tab: overview, services, and worker jobs you can run, pause and restart", async () => {
  const calls = mockApi({
    "GET /api/system/status": [{ key: "directory", label: "Directory sync", state: "error", summary: "Last sync failed: Can't contact LDAP server", at: at() }],
    "GET /api/system/services": SERVICES,
    "POST /api/system/jobs/email/run": { ...SERVICES, jobs: [job("email", "Email: check the mailbox and send", { run_requested: true }), SERVICES.jobs[1]] },
    "PUT /api/system/jobs/directory": { ...SERVICES, jobs: [SERVICES.jobs[0], { ...SERVICES.jobs[1], paused: true }] },
    "POST /api/system/worker/restart": { ...SERVICES, worker: { ...SERVICES.worker, restart_requested_at: at(), restart_requested_by: "M. Osei" } },
  });
  renderAt("/", [{ path: "/", element: <StatusAdmin me={ADMIN} /> }]);
  const overview = await screen.findByRole("region", { name: "Overview" });
  expect(await within(overview).findByText(/Can't contact LDAP server/)).toBeInTheDocument();

  const jobs = await screen.findByRole("region", { name: "Worker jobs" });
  expect(within(jobs).getByText("Failed")).toHaveClass("chip", "r");
  expect(within(jobs).getByText("RuntimeError: Can't contact LDAP server")).toBeInTheDocument();
  await userEvent.click(within(jobs).getByRole("button", { name: "Run Email: check the mailbox and send now" }));
  expect(await within(jobs).findByText("Starting…")).toBeInTheDocument();
  await userEvent.click(within(jobs).getByRole("button", { name: "Pause Directory sync" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ paused: true }));

  // No force-restart command on this server: only the normal restart.
  expect(screen.queryByRole("button", { name: "Force restart" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Restart worker" }));
  expect(await screen.findByText(/Restart asked for by M. Osei/)).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/system/worker/restart")?.body).toEqual({ force: false });
});

test("ticket fields: add, rename and retire choices; import departments from AD", async () => {
  const opt = (id: number, label: string, is_active = true) => ({ id, list: "department", label, position: id, is_active, key: null });
  const calls = mockApi({
    "GET /api/settings/ticket_fields": { section: "ticket_fields", fields: [] },
    "GET /api/field-options": ({ path }: Call) => (path.includes("list=department") ? [opt(5, "Finance"), opt(6, "Old team", false)] : []),
    "POST /api/field-options": opt(7, "HR"),
    "PATCH /api/field-options/5": opt(5, "Finance", false),
    "POST /api/field-options/departments/import": [opt(5, "Finance"), opt(8, "IT")],
  });
  renderAt("/", [{ path: "/", element: <TicketFieldsAdmin me={ADMIN} /> }]);
  const depts = await screen.findByRole("region", { name: "Departments" });
  expect(await within(depts).findByText("Finance")).toBeInTheDocument();
  expect(within(depts).getByText("(retired)")).toBeInTheDocument();
  expect(within(depts).getByRole("button", { name: "Restore" })).toBeInTheDocument();

  await userEvent.type(within(depts).getByLabelText("New department"), "HR");
  await userEvent.click(within(depts).getByRole("button", { name: "Add" }));
  await waitFor(() => expect(calls.find((c) => c.path === "/api/field-options" && c.method === "POST")?.body).toEqual({ list: "department", label: "HR" }));
  await userEvent.click(within(depts).getAllByRole("button", { name: "Retire" })[0]);
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ is_active: false }));
  await userEvent.click(within(depts).getByRole("button", { name: "Import from AD" }));
  expect(await within(depts).findByText("Departments imported from AD.")).toBeInTheDocument();
});

test("branding: upload a background by dropping it, see it previewed, remove it", async () => {
  const BRAND = { product_name: "Example Help Desk", sign_in_message: "", background_in_console: false, background_url: null, logo_url: null };
  const calls = mockApi({
    "GET /api/branding": BRAND,
    "GET /api/settings/branding": { section: "branding", fields: [] },
    "PUT /api/settings/branding/files/background": { ...BRAND, background_url: "/api/branding/files/background?v=1" },
    "DELETE /api/settings/branding/files/background": BRAND,
  });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const slot = await screen.findByRole("region", { name: "Background" });
  expect(within(slot).getByRole("img", { name: "No background" })).toBeInTheDocument();
  const zone = slot.querySelector(".image-slot")!;
  fireEvent.drop(zone, { dataTransfer: { types: ["Files"], files: [new File(["GIF89a"], "office.gif", { type: "image/gif" })] } });
  expect(await within(slot).findByRole("img", { name: "Current background" })).toHaveStyle({ backgroundImage: 'url("/api/branding/files/background?v=1")' });
  expect((calls.find((c) => c.method === "PUT")!.body as FormData).get("file")).toBeInstanceOf(File);
  await userEvent.click(within(slot).getByRole("button", { name: "Remove" }));
  expect(await within(slot).findByRole("img", { name: "No background" })).toBeInTheDocument();
});

test("notices: post one; it shows after sign-in and as a banner", async () => {
  const NOTICE = { id: 1, title: "Email is down", message: "We're on it.", severity: "outage", audience: "everyone", starts_at: new Date().toISOString(), ends_at: null, created_by: null, updated_at: new Date().toISOString(), state: "current" };
  const calls = mockApi({
    "GET /api/notices": [],
    "POST /api/notices": NOTICE,
  });
  renderAt("/", [{ path: "/", element: <NoticesAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Post a notice" }));
  const form = screen.getByRole("form", { name: "New notice" });
  await userEvent.type(within(form).getByLabelText("Title"), "Email is down");
  await userEvent.selectOptions(within(form).getByLabelText("Severity"), "outage");
  await userEvent.click(within(form).getByRole("button", { name: "Post notice" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ title: "Email is down", severity: "outage", audience: "everyone", starts_at: null, ends_at: null }));
});

test("current notices: a dialog once per tab, then a banner that can be hidden", async () => {
  sessionStorage.clear();
  const NOTICE = { id: 7, title: "VPN is flaky", message: "Use Wi-Fi in the office meanwhile.", severity: "warning", audience: "everyone", starts_at: new Date().toISOString(), ends_at: null, created_by: null, updated_at: new Date().toISOString(), state: "current" };
  mockApi({ "GET /api/notices/current": [NOTICE] });
  const view = renderAt("/", [{ path: "/", element: <><NoticeBanner /><NoticeDialog /></> }]);
  const dialog = await screen.findByRole("dialog", { name: "Service notice" });
  expect(within(dialog).getByText("VPN is flaky")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "OK" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const banner = screen.getByRole("region", { name: "Service notices" });
  expect(within(banner).getByText("Warning")).toBeInTheDocument();
  view.unmount();
  // Same tab, later: no dialog again, banner still there until hidden.
  renderAt("/", [{ path: "/", element: <><NoticeBanner /><NoticeDialog /></> }]);
  await screen.findByRole("region", { name: "Service notices" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Hide notice: VPN is flaky" }));
  expect(screen.queryByRole("region", { name: "Service notices" })).not.toBeInTheDocument();
});

test("branding previews both appearances before saving the organization theme", async () => {
  const fields = customThemeFields();
  const calls = mockApi({ "GET /api/branding": { product_name: "Layer 8" }, "GET /api/settings/theme": { section: "theme", fields }, "PUT /api/settings/theme": { section: "theme", fields } });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Colours" });
  expect(within(form).getByRole("img", { name: "Light preview" })).toBeInTheDocument();
  expect(within(form).getByRole("img", { name: "Dark preview" })).toBeInTheDocument();
  await userEvent.selectOptions(within(form).getByLabelText("Default technician theme"), "amethyst");
  expect(within(form).getByRole("img", { name: "Light preview" })).toHaveStyle({ "--brand": "#6547a9" });
  expect(calls.filter(c => c.method === "PUT")).toHaveLength(0);
  await userEvent.click(within(form).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find(c => c.method === "PUT")?.body).toMatchObject({ values: { console_theme: "amethyst" } }));
});

test("backup: list backups, back up now, verify one", async () => {
  const JOB = { name: "backup", label: "Backup", paused: false, running: false, run_requested: false, last_started_at: null, last_finished_at: "2026-10-19T02:00:05Z", last_ok: true, last_summary: "helpdesk-backup-20261019-020000.hdbackup (3.2 MB) written and verified", last_error: null, next_due_at: "2026-10-20T02:00:00Z" };
  const SERVICES = { api: { host: "h", pid: 1, started_at: "2026-10-19T00:00:00Z", version: "0.1.0" }, worker: { state: "running" }, jobs: [JOB] };
  const calls = mockApi({
    "GET /api/settings/backup": { section: "backup", fields: [] },
    "GET /api/settings/backup/files": { folder: "/mnt/backups/helpdesk", files: [{ name: "helpdesk-backup-20261019-020000.hdbackup", size: 3_355_443, created_at: "2026-10-19T02:00:05Z" }], problem: null },
    "GET /api/system/services": SERVICES,
    "POST /api/system/jobs/backup/run": { ...SERVICES, jobs: [{ ...JOB, run_requested: true }] },
    "POST /api/settings/backup/files/helpdesk-backup-20261019-020000.hdbackup/verify": { name: "helpdesk-backup-20261019-020000.hdbackup", created_at: "2026-10-19T02:00:00+00:00", host: "helpdesk01", schema_revision: "abc", files: 42, counts: { tickets: 1200, users: 310 } },
  });
  renderAt("/", [{ path: "/", element: <BackupAdmin me={ADMIN} /> }]);
  const list = await screen.findByRole("region", { name: "Backups" });
  expect(await within(list).findByText("helpdesk-backup-20261019-020000.hdbackup")).toBeInTheDocument();
  expect(within(list).getByText(/written and verified/)).toBeInTheDocument();
  await userEvent.click(within(list).getByRole("button", { name: "Verify" }));
  expect(await within(list).findByText(/is whole: .* on helpdesk01, 42 files, 1200 tickets, 310 users/)).toBeInTheDocument();
  await userEvent.click(within(list).getByRole("button", { name: "Back up now" }));
  expect(await within(list).findByRole("button", { name: "Backing up…" })).toBeDisabled();
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/system/jobs/backup/run")).toBe(true);
});

test("relays: add one and see its token once", async () => {
  const R = { id: 1, name: "RUNNER01", description: "", languages: ["powershell", "eos"], is_active: true, last_seen_at: null, last_host: null, last_version: null, created_at: "2026-10-19T00:00:00Z" };
  const calls = mockApi({
    "GET /api/runners": [],
    "POST /api/runners": { runner: R, token: "hdr_secret-token" },
  });
  renderAt("/", [{ path: "/", element: <RunnersAdmin me={ADMIN} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Add a relay" }));
  const form = screen.getByRole("form", { name: "Add a relay" });
  await userEvent.type(within(form).getByLabelText("Name"), "RUNNER01");
  await userEvent.click(within(form).getByLabelText("Arista EOS"));
  await userEvent.click(within(form).getByLabelText("Dedicated integration relay"));
  await userEvent.click(within(form).getByRole("button", { name: "Add relay" }));
  const card = await screen.findByRole("region", { name: "Relay token" });
  expect(within(card).getByText("hdr_secret-token")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "RUNNER01", languages: ["powershell", "eos"], builtins_only: true });
});

test("a technician who was handed areas sees their tabs", async () => {
  mockApi({ "GET /api/auth/me": { ...ME, permissions: [...(ME.permissions ?? []), "rules_manage", "notices_manage"] }, "GET /api/notices/all": [] });
  mountApp("/settings/groups");
  const tabs = await screen.findByRole("navigation", { name: "Settings" });
  expect([...tabs.querySelectorAll("a")].map((a) => a.textContent)).toEqual(["Queues", "Ticket rules", "Canned replies", "Notices", "Users", "Groups"]);
});

test("Action1's tab shows only while Action1 is in use", async () => {
  mockApi({ "GET /api/auth/me": { ...ADMIN, features: [] }, "GET /api/settings/windows-sign-in": EMPTY });
  mountApp("/settings/windows-sign-in");
  await screen.findByRole("heading", { name: "Windows sign-in" });
  const tabs = [...screen.getByRole("navigation", { name: "Settings" }).querySelectorAll("a")].map((a) => a.textContent);
  expect(tabs).not.toContain("Action1");
  expect(tabs).toContain("Device sweeps");
});


test("API keys: a new key is shown once", async () => {
  const KEY = { id: 1, name: "MER server room", prefix: "l8k_AbCd", is_active: true, created_at: "2026-10-22T00:00:00Z", last_used_at: null, created_by: "Admin" };
  const calls = mockApi({
    "GET /api/auth/me": ADMIN,
    "GET /api/api-keys": [],
    "POST /api/api-keys": { ...KEY, token: "l8k_AbCdSecretSecret" },
  });
  mountApp("/settings/api-keys");
  await userEvent.type(await screen.findByLabelText("Key name"), "MER server room");
  await userEvent.click(screen.getByRole("button", { name: "Make a key" }));
  const card = await screen.findByRole("region", { name: "New API key" });
  expect(card).toHaveTextContent("l8k_AbCdSecretSecret");
  expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "MER server room" });
});

test("legacy console palette and custom-colour controls are no longer offered", async () => {
  mockApi({ "GET /api/branding": {}, "GET /api/settings/theme": { section: "theme", fields: [...customThemeFields(), { name: "theme_preset", label: "Old appearance", kind: "choice", value: "midnight", choices: [{ value: "midnight", label: "Midnight" }], locked: false }] } });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Colours" });
  expect(within(form).queryByLabelText("Old appearance")).not.toBeInTheDocument();
  expect(within(form).getByLabelText("Default technician theme")).toBeInTheDocument();
});

test("requester email templates show a preview with sample values", async () => {
  const { fillSample } = await import("./RequesterEmailsAdmin");
  expect(fillSample("Hi {first_name}, {ticket} is resolved. {unknown}")).toBe("Hi Ana, #1042 is resolved. {unknown}");
});


const customThemeFields = (locked = false) => [
  { name: "console_theme", label: "Default technician theme", kind: "choice", value: "blue-steel", choices: [{ value: "tidepool", label: "Tidepool" }, { value: "amethyst", label: "Amethyst" }, { value: "blue-steel", label: "Blue Steel" }] },
  { name: "dark_mode", label: "Dark mode", kind: "choice", value: "on", choices: [{ value: "off", label: "Off" }, { value: "on", label: "On" }] },
].map(f => ({ ...f, help: "", locked: locked && f.name === "dark_mode" }));

test("branding restores Tidepool light atomically and keeps uploaded branding", async () => {
  const defaults = { console_theme: "tidepool", dark_mode: "off" };
  const calls = mockApi({
    "GET /api/branding": { product_name: "Example Help Desk", logo_url: "/api/branding/files/logo?v=1", background_url: "/api/branding/files/background?v=1" },
    "GET /api/settings/theme": { section: "theme", fields: customThemeFields() },
    "PUT /api/settings/theme": { section: "theme", fields: customThemeFields().map(f => ({ ...f, value: defaults[f.name as keyof typeof defaults] })) },
  });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Colours" });
  await userEvent.selectOptions(within(form).getByLabelText("Default technician theme"), "amethyst");
  await userEvent.click(within(form).getByRole("button", { name: "Restore default light theme" }));
  expect(await within(form).findByText("Saved.")).toBeInTheDocument();
  expect(calls.filter(c => c.method === "PUT")).toEqual([{ method: "PUT", path: "/api/settings/theme", body: { values: defaults, clear: [] } }]);
  expect(within(form).getByLabelText("Default technician theme")).toHaveValue("tidepool");
  expect(within(form).getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByRole("img", { name: "Current logo" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Current background" })).toBeInTheDocument();
});

test("branding cannot restore defaults over server-locked colour settings", async () => {
  const calls = mockApi({
    "GET /api/branding": {},
    "GET /api/settings/theme": { section: "theme", fields: customThemeFields(true) },
  });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const button = await screen.findByRole("button", { name: "Restore default light theme" });
  expect(button).toBeDisabled();
  expect(screen.getByText(/Default colours cannot be restored/)).toBeInTheDocument();
  await userEvent.click(button);
  expect(calls.some(c => c.method === "PUT")).toBe(false);
});

test("failed theme restore retains the unsaved palette for retry", async () => {
  mockApi({ "GET /api/branding": {}, "GET /api/settings/theme": { section: "theme", fields: customThemeFields() }, "PUT /api/settings/theme": () => new Response(JSON.stringify({ detail: "Could not save settings." }), { status: 503 }) });
  renderAt("/", [{ path: "/", element: <BrandingAdmin me={ADMIN} /> }]);
  const form = await screen.findByRole("form", { name: "Colours" });
  await userEvent.selectOptions(within(form).getByLabelText("Default technician theme"), "amethyst");
  await userEvent.click(within(form).getByRole("button", { name: "Restore default light theme" }));
  expect(await within(form).findByRole("alert")).toHaveTextContent("Could not save settings.");
  expect(within(form).getByLabelText("Default technician theme")).toHaveValue("amethyst");
  expect(within(form).getByRole("button", { name: "Save" })).toBeEnabled();
});
