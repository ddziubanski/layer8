// SPDX-License-Identifier: AGPL-3.0-or-later
// Thin typed wrapper over the API. Types come from the backend's OpenAPI schema
// (npm run api-types), so an API change that breaks the UI fails the build.
import type { components } from "./schema";

type S = components["schemas"];
export type UpdateStatus = S["UpdateStatusOut"];
export type ImportReport = S["ImportReport"];
export type SoftwareDeployment = S["DeploymentOut"];
export type SoftwarePackage = S["SoftwarePackageOut"];
export type Ticket = S["TicketOut"];
export type NutanixVm = S["NutanixVmOut"];
export type CannedReply = S["CannedReplyOut"];
export type TicketPage = S["TicketPage"];
export type Comment = S["CommentOut"];
export type TicketEvent = S["EventOut"];
export type Attachment = S["AttachmentOut"];
export type Queue = S["QueueOut"];
export type User = S["UserOut"];
/** The signed-in user; portal_only after Windows sign-in (the console needs a password). */
export type LibraryReference = S["LibraryReference"];
export type Me = S["MeOut"];
export type QueueListPreference = S["QueueListPreferences"];
export type Person = S["PersonOut"];
export type TicketTemplate = S["TicketTemplateOut"];
export type MerakiObservation = S["MerakiClientOut"];
export type SignInOptions = S["SignInOptionsOut"];
export type UserRef = S["UserRef"];
export type SavedView = S["SavedView"];
export type Priority = S["Priority"];
export type TicketStatus = S["TicketStatus"];
export type TicketSource = S["TicketSource"];
export type TicketCreate = S["TicketCreate"];
export type TicketUpdate = S["TicketUpdate"];
export type AssignIn = S["AssignIn"];
export type QueueUpdate = S["QueueUpdate"];
export type QueueCreate = S["QueueCreate"];
export type Group = S["GroupOut"];
export type PortalQueue = S["PortalQueueOut"];
export type PortalHours = S["PortalHoursOut"];
export type PortalTicket = S["PortalTicketOut"];
export type PortalTicketPage = S["PortalTicketPage"];
export type PortalTicketCreate = S["PortalTicketCreate"];
export type TicketRule = S["TicketRuleOut"];
export type TicketRuleIn = S["TicketRuleIn"];
export type RuleRun = S["RuleRunOut"];
export type RuleCatalog = S["RuleCatalogOut"];
export type DirectoryStatus = S["DirectoryStatusOut"];
export type DirectoryGroupHit = S["DirectoryGroupOut"];
export type DirectoryLookup = S["DirectoryLookupOut"];
export type RoleMapping = S["RoleMappingOut"];
export type RoleMappingIn = S["RoleMappingIn"];
export type RolePreview = S["RolePreviewOut"];
export type SyncRun = S["DirectorySyncRunOut"];
export type UserRole = S["UserRole"];
export type Branding = S["BrandingOut"];
export type Notice = S["NoticeOut"];
export type NoticeIn = S["NoticeIn"];
export type FieldOption = S["FieldOptionOut"];
export type FieldList = S["FieldList"];
export type TicketSort = NonNullable<NonNullable<import("./schema").operations["list_tickets_api_tickets_get"]["parameters"]["query"]>["sort"]>;
export type WindowsSignIn = S["WindowsSignInOut"];
export type SettingsSection = S["SettingsSectionOut"];
export type SettingField = S["SettingFieldOut"];
export type EmailTest = S["EmailTestOut"];
export type MailPass = S["MailPassOut"];
export type StatusItem = S["StatusItemOut"];
export type IntegrationHealth = S["IntegrationHealthOut"];
export type SystemEvent = S["SystemEventOut"];
export type LogSource = S["LogSourceOut"];
export type Services = S["ServicesOut"];
export type BackupList = S["BackupListOut"];
export type Device = S["DeviceOut"];
export type Vendor = S["VendorOut"];
export type ADGroup = S["ADGroupOut"];
export type ADChange = S["ADChangeIn"];
export type ADPlan = S["ADPlanOut"];
export type ADGroupList = S["ADGroupListOut"];
// Older saved layouts and fallback layouts do not carry the widget opt-in flag.
export type Layout = Omit<S["LayoutOut"], "widget_mode"> & { widget_mode?: boolean };
export type PageLayout = S["PageLayoutOut"];
export type NewADAccount = S["NewADAccountIn"];
export type NewADAccountPlan = S["NewADAccountPlanOut"];
export type NewADAccountMade = S["NewADAccountOut"];
export type OnboardingTemplate = S["OnboardingTemplateOut"];
export type OnboardingTemplateIn = S["OnboardingTemplateIn"];
export type PO = S["PODetailOut"];
export type POSummary = S["POSummaryOut"];
export type POIn = S["POIn"];
export type PublicPO = S["PublicPOOut"];
export type TicketExpenses = S["TicketExpensesOut"];
export type ExpenseIn = S["ExpenseIn"];
export type VendorIn = S["VendorIn"];
export type VendorContact = S["VendorContactOut"];
export type SystemItem = S["SystemOut"];
export type SystemIn = S["SystemIn"];
export type TicketSystem = S["TicketSystemOut"];
export type Vlan = S["VlanOut"];
export type VlanIn = S["VlanIn"];
export type Subnet = S["SubnetOut"];
export type SubnetDetail = S["SubnetDetailOut"];
export type SubnetIn = S["SubnetIn"];
export type IpAddress = S["IpAddressOut"];
export type IpAddressIn = S["IpAddressIn"];
export type IpScan = S["IpScanOut"];
export type IpFinding = S["IpFindingOut"];
export type ScanRunner = S["ScanRunnerOut"];
export type DeviceIp = S["DeviceIpOut"];
export type SwitchPort = S["SwitchPortOut"];
export type DeviceWarranty = S["DeviceWarrantyOut"];
export type ApiKey = S["ApiKeyOut"];
export type ApiKeyMade = S["ApiKeyMadeOut"];
export type Wallboard = S["WallboardOut"];
export type WallboardMade = S["WallboardMadeOut"];
export type WallboardIn = S["WallboardIn"];
export type WallboardData = S["BoardData"];
export type NetworkDevice = S["NetworkDeviceOut"];
export type NetworkDeviceDetail = S["NetworkDeviceDetailOut"];
export type DevicePatch = S["DevicePatchOut"];
export type Sweep = S["SweepOut"];
export type Lifecycle = S["LifecycleOut"];
export type ChecklistTemplate = S["ChecklistTemplateOut"];
export type ChecklistTemplateIn = S["ChecklistTemplateIn"];
export type TicketChecklist = S["TicketChecklistOut"];
export type Change = S["ChangeOut"];
export type KbArticle = S["KbArticleOut"];
export type Survey = S["SurveyOut"];
export type PermissionRule = S["PermissionOut"];
export type PermissionRuleIn = S["PermissionRuleIn"];
export type SurveyAnswer = S["SurveyAnswerIn"];
export type SurveySummary = S["SurveySummaryOut"];
export type KbArticleSummary = S["KbArticleSummaryOut"];
export type KbArticleIn = S["KbArticleIn"];
export type TicketKb = S["TicketKbOut"];
export type ChangeSummary = S["ChangeSummaryOut"];
export type ChangeIn = S["ChangeIn"];
export type ChangeTemplate = S["ChangeTemplateOut"];
export type ChangeTemplateIn = S["ChangeTemplateIn"];
export type StartChecklist = S["StartChecklistIn"];
export type Action1Status = S["Action1StatusOut"];
export type Script = S["ScriptOut"];
export type TicketScope = S["TicketScope"];
export type ColorRule = S["ColorRule"];
export type Runner = S["RunnerOut"];
export type RemoteSession = S["RemoteSessionOut"];
export type RemoteLaunch = S["RemoteLaunchOut"];
export type PortalDevice = S["PortalDeviceOut"];
export type RunnerIn = S["RunnerIn"];
export type RunnerToken = S["RunnerTokenOut"];
export type Run = S["RunOut"];
export type RunDetail = S["RunDetailOut"];
export type RunRequest = S["RunRequestIn"];
export type RunPreview = S["RunPreviewOut"];
export type ScriptIn = S["ScriptIn"];
export type ScriptParam = S["ScriptParam"];
export type ScriptVersion = S["ScriptVersionOut"];
export type ScriptVersionDetail = S["ScriptVersionDetailOut"];
export type ScriptQueryRun = S["ScriptQueryRunIn"];
export type DeviceDetail = S["DeviceDetailOut"];
export type DevicePage = S["DevicePage"];
export type DeviceIn = S["DeviceIn"];
export type DeviceUpdate = S["DeviceUpdateIn"];
export type TicketDevice = S["TicketDeviceOut"];
export type BackupVerify = S["BackupVerifyOut"];
export type ReportSpec = S["ReportSpec"];
export type ReportResult = S["ReportResult"];
export type ReportPreset = S["ReportPresetOut"];
export type ReportDataset = S["ReportDatasetOut"];
export type SavedReport = S["SavedReportOut"];
export type SavedReportIn = S["SavedReportIn"];
export type ReportEmailIn = S["ReportEmailIn"];
export type ReportSchedule = S["ReportScheduleOut"];
export type ReportScheduleIn = S["ReportScheduleIn"];
export type ScriptSchedule = S["ScriptScheduleOut"];
export type ScriptScheduleIn = S["ScriptScheduleIn"];
export type WorkerJob = S["JobOut"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailText(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{loc, msg}, ...]
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
  return `Request failed (${status})`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  // Required by the API for cookie-authenticated writes (CSRF protection).
  if (method !== "GET") headers["X-Helpdesk-CSRF"] = "1";
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailText(data, res.status));
  return data as T;
}

/** POST, then save the response as a file (the server's file name wins when it gives one). */
async function download(path: string, body: unknown, fallbackName: string): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Helpdesk-CSRF": "1" },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!res.ok) throw new ApiError(res.status, detailText(await res.json().catch(() => null), res.status));
  const named = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1];
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = named ?? fallbackName;
  a.click();
  URL.revokeObjectURL(url);
}

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  teams: {
    connection: () => request<S["ConnectionOut"]>("GET", "/api/teams/connection"),
    connect: () => request<S["StartOut"]>("POST", "/api/teams/connect"),
    complete: (body: S["CallbackIn"]) => request<S["ConnectionOut"]>("POST", "/api/teams/callback", body),
    disconnect: () => request<void>("DELETE", "/api/teams/connection"),
    chats: (id: number) => request<S["ChatOut"][]>("GET", `/api/tickets/${id}/teams/chats`),
    send: (id: number, body: S["SendIn"]) => request<S["SentOut"]>("POST", `/api/tickets/${id}/teams/messages`, body),
    copilotContext: (id: number, internal: boolean) => request<S["CopilotContextOut"]>("GET", `/api/tickets/${id}/copilot/context?include_internal=${internal}`),
    copilot: (id: number, body: S["CopilotIn"]) => request<S["CopilotOut"]>("POST", `/api/tickets/${id}/copilot`, body),
  },
  me: () => request<Me>("GET", "/api/auth/me"),
  imports: {
    kace: (file: File, queue: number, fingerprint?: string) => {
      const form = new FormData();
      form.append("file", file); form.append("queue_id", String(queue));
      if (fingerprint) { form.append("fingerprint", fingerprint); form.append("confirm", "true"); }
      return request<ImportReport>("POST", `/api/imports/kace/${fingerprint ? "apply" : "preview"}`, form);
    },
  },
  branding: () => request<Branding>("GET", "/api/branding"),
  about: () => request<{ product: string; version: string; license: string; source_download: string | null; source_repository: string | null }>("GET", "/api/about"),
  notices: {
    current: () => request<Notice[]>("GET", "/api/notices/current"),
    all: () => request<Notice[]>("GET", "/api/notices"),
    post: (body: NoticeIn) => request<Notice>("POST", "/api/notices", body),
    update: (id: number, body: NoticeIn) => request<Notice>("PUT", `/api/notices/${id}`, body),
    end: (id: number) => request<Notice>("POST", `/api/notices/${id}/end`),
    remove: (id: number) => request<void>("DELETE", `/api/notices/${id}`),
  },
  uploadBrandingImage: (name: "background" | "logo" | "favicon", file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Branding>("PUT", `/api/settings/branding/files/${name}`, form);
  },
  removeBrandingImage: (name: "background" | "logo" | "favicon") => request<Branding>("DELETE", `/api/settings/branding/files/${name}`),
  signInOptions: () => request<SignInOptions>("GET", "/api/auth/options"),
  login: (email: string, password: string, code?: string) => request<Me>("POST", "/api/auth/login", code ? { email, password, code } : { email, password }),
  twoFactor: {
    status: () => request<S["TwoFactorStatusOut"]>("GET", "/api/auth/two-factor"),
    setup: () => request<S["TwoFactorSetupOut"]>("POST", "/api/auth/two-factor/setup"),
    confirm: (code: string) => request<S["TwoFactorRecoveryOut"]>("POST", "/api/auth/two-factor/confirm", { code }),
    off: (password: string, code: string) => request<void>("POST", "/api/auth/two-factor/off", { password, code }),
    reset: (userId: number) => request<void>("DELETE", `/api/users/${userId}/two-factor`),
  },
  changePassword: (current: string, next: string) => request<void>("POST", "/api/auth/password", { current_password: current, new_password: next }),
  // The browser answers the server's Kerberos challenge itself when it trusts this site.
  windowsSignIn: () => request<Me>("POST", "/api/auth/sso"),
  logout: () => request<void>("POST", "/api/auth/logout"),

  tickets: (p: { view?: SavedView; scope?: TicketScope; queue_id?: number; requester_id?: number; q?: string; sort?: TicketSort; dir?: "asc" | "desc"; limit?: number; offset?: number }) =>
    request<TicketPage>("GET", `/api/tickets${query(p)}`),
  unaccepted: () => request<S["UnacceptedOut"]>("GET", "/api/tickets/unaccepted"),
  person: (id: number) => request<Person>("GET", `/api/people/${id}`),
  viewCounts: (scope: TicketScope = "all", queueId?: number) => request<Record<SavedView, number>>("GET", `/api/tickets/views${query({ scope, queue_id: queueId })}`),
  bulkAssign: (body: S["BulkAssignIn"]) => request<S["BulkResultOut"]>("POST", "/api/tickets/bulk/assign", body),
  savePreferences: (body: S["PreferencesIn"]) => request<Me>("PUT", "/api/auth/me/preferences", body),
  ticket: (id: number) => request<Ticket>("GET", `/api/tickets/${id}`),
  createTicket: (body: TicketCreate) => request<Ticket>("POST", "/api/tickets", body),
  merge: (id: number, body: S["MergeIn"]) => request<Ticket>("POST", `/api/tickets/${id}/merge`, body),
  children: (id: number) => request<S["ChildTicketOut"][]>("GET", `/api/tickets/${id}/children`),
  related: (id: number) => request<S["RelatedTicketOut"][]>("GET", `/api/tickets/${id}/related`),
  updateTicket: (id: number, body: TicketUpdate) => request<Ticket>("PATCH", `/api/tickets/${id}`, body),
  assign: (id: number, body: AssignIn) => request<Ticket>("POST", `/api/tickets/${id}/assign`, body),
  escalate: (id: number, reason: string) => request<Ticket>("POST", `/api/tickets/${id}/escalate`, { reason }),

  comments: (id: number) => request<Comment[]>("GET", `/api/tickets/${id}/comments`),
  addComment: (id: number, body: string, isInternal: boolean, emailRequester = true) =>
    request<Comment>("POST", `/api/tickets/${id}/comments`, { body, is_internal: isInternal, email_requester: emailRequester }),
  events: (id: number) => request<TicketEvent[]>("GET", `/api/tickets/${id}/events`),

  attachments: (id: number) => request<Attachment[]>("GET", `/api/tickets/${id}/attachments`),
  upload: (id: number, file: File, commentId?: number) => {
    const form = new FormData();
    form.append("file", file);
    if (commentId !== undefined) form.append("comment_id", String(commentId));
    return request<Attachment>("POST", `/api/tickets/${id}/attachments`, form);
  },
  downloadUrl: (attachmentId: string) => `/api/attachments/${attachmentId}/download`,
  viewUrl: (attachmentId: string) => `/api/attachments/${attachmentId}/view`,

  /** Pick an existing user, or link by email (a requester account is made if there's none). */
  changeRequester: (id: number, who: { user_id: number } | { email: string; display_name?: string | null }) =>
    request<Ticket>("POST", `/api/tickets/${id}/requester`, who),
  /** Active people whose name or email contains q (the requester picker). */
  searchUsers: (q: string) => request<User[]>("GET", `/api/users${query({ q, limit: 12 })}`),

  fieldOptions: (list: FieldList, includeRetired = false) =>
    request<FieldOption[]>("GET", `/api/field-options${query({ list, include_retired: includeRetired ? "true" : undefined })}`),
  addFieldOption: (list: FieldList, label: string) => request<FieldOption>("POST", "/api/field-options", { list, label }),
  updateFieldOption: (id: number, body: { label?: string; is_active?: boolean }) => request<FieldOption>("PATCH", `/api/field-options/${id}`, body),
  reorderFieldOptions: (list: FieldList, ids: number[]) => request<FieldOption[]>("PUT", `/api/field-options/order/${list}`, ids),
  importDepartments: () => request<FieldOption[]>("POST", "/api/field-options/departments/import"),

  queues: () => request<Queue[]>("GET", "/api/queues"),
  createQueue: (body: QueueCreate) => request<Queue>("POST", "/api/queues", body),
  updateQueue: (id: number, body: QueueUpdate) => request<Queue>("PATCH", `/api/queues/${id}`, body),
  setOwners: (id: number, userIds: number[], groupIds: number[]) =>
    request<Queue>("PUT", `/api/queues/${id}/owners`, { user_ids: userIds, group_ids: groupIds }),
  setManagers: (id: number, userIds: number[], groupIds: number[]) =>
    request<Queue>("PUT", `/api/queues/${id}/managers`, { user_ids: userIds, group_ids: groupIds }),
  setViewers: (id: number, userIds: number[], groupIds: number[]) =>
    request<Queue>("PUT", `/api/queues/${id}/viewers`, { user_ids: userIds, group_ids: groupIds }),
  groups: () => request<Group[]>("GET", "/api/groups"),
  createGroup: (name: string, description: string | null) => request<Group>("POST", "/api/groups", { name, description, source: "local" }),
  updateGroup: (id: number, body: { name?: string; description?: string | null; is_active?: boolean }) => request<Group>("PATCH", `/api/groups/${id}`, body),
  setGroupMembers: (id: number, userIds: number[]) => request<Group>("PUT", `/api/groups/${id}/members`, { user_ids: userIds }),
  assignable: (queueId: number) => request<UserRef[]>("GET", `/api/queues/${queueId}/assignable`),
  users: () => request<User[]>("GET", "/api/users"),

  rules: {
    catalog: () => request<RuleCatalog>("GET", "/api/ticket-rules/catalog"),
    list: () => request<TicketRule[]>("GET", "/api/ticket-rules"),
    create: (body: TicketRuleIn) => request<TicketRule>("POST", "/api/ticket-rules", body),
    save: (id: number, body: TicketRuleIn) => request<TicketRule>("PUT", `/api/ticket-rules/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/ticket-rules/${id}`),
    test: (id: number, ticketId?: number) => request<RuleRun>("POST", `/api/ticket-rules/${id}/test${query({ ticket_id: ticketId })}`),
    run: (id: number) => request<RuleRun>("POST", `/api/ticket-rules/${id}/run`),
    runs: (id: number) => request<RuleRun[]>("GET", `/api/ticket-rules/${id}/runs`),
  },

  directory: {
    status: () => request<DirectoryStatus>("GET", "/api/directory/status"),
    test: () => request<Record<string, string>>("POST", "/api/directory/test"),
    lookup: (login: string) => request<DirectoryLookup>("POST", "/api/directory/lookup", { login }),
    searchGroups: (q: string) => request<DirectoryGroupHit[]>("GET", `/api/directory/groups/search${query({ q })}`),
    importGroup: (guid: string) => request<Group>("POST", "/api/directory/groups/import", { guid }),
    mappings: () => request<RoleMapping[]>("GET", "/api/directory/role-mappings"),
    preview: (body: RoleMappingIn[]) => request<RolePreview>("POST", "/api/directory/role-mappings/preview", body),
    saveMappings: (body: RoleMappingIn[]) => request<RoleMapping[]>("PUT", "/api/directory/role-mappings", body),
    sync: (dryRun: boolean) => request<SyncRun>("POST", `/api/directory/sync${query({ dry_run: dryRun ? "true" : undefined })}`),
    runs: () => request<SyncRun[]>("GET", "/api/directory/runs"),
  },

  reports: {
    presets: () => request<ReportPreset[]>("GET", "/api/reports/presets"),
    datasets: () => request<{ name: string; label: string }[]>("GET", "/api/reports/datasets"),
    dataset: (name: string) => request<ReportDataset>("GET", `/api/reports/datasets/${name}`),
    run: (spec: ReportSpec) => request<ReportResult>("POST", "/api/reports/run", spec),
    saved: () => request<SavedReport[]>("GET", "/api/reports/saved"),
    save: (body: SavedReportIn) => request<SavedReport>("POST", "/api/reports/saved", body),
    update: (id: number, body: SavedReportIn) => request<SavedReport>("PUT", `/api/reports/saved/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/reports/saved/${id}`),
    downloadPdf: (body: { spec: ReportSpec; title: string; description?: string }) => download("/api/reports/run.pdf", body, "report.pdf"),
    email: (body: ReportEmailIn) => request<{ queued: number }>("POST", "/api/reports/email", body),
    periods: () => request<Record<string, string>>("GET", "/api/reports/periods"),
    schedules: () => request<ReportSchedule[]>("GET", "/api/reports/schedules"),
    createSchedule: (body: ReportScheduleIn) => request<ReportSchedule>("POST", "/api/reports/schedules", body),
    updateSchedule: (id: number, body: ReportScheduleIn) => request<ReportSchedule>("PUT", `/api/reports/schedules/${id}`, body),
    deleteSchedule: (id: number) => request<void>("DELETE", `/api/reports/schedules/${id}`),
    sendSchedule: (id: number) => request<{ queued: number }>("POST", `/api/reports/schedules/${id}/send`),
    /** The report as a CSV file download. */
    downloadCsv: (spec: ReportSpec, filename: string) => download("/api/reports/run.csv", spec, filename),
  },

  scripts: {
    list: () => request<Script[]>("GET", "/api/scripts"),
    create: (body: ScriptIn) => request<Script>("POST", "/api/scripts", body),
    update: (id: number, body: ScriptIn) => request<Script>("PUT", `/api/scripts/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/scripts/${id}`),
    versions: (id: number) => request<ScriptVersion[]>("GET", `/api/scripts/${id}/versions`),
    version: (id: number, n: number) => request<ScriptVersionDetail>("GET", `/api/scripts/${id}/versions/${n}`),
    restore: (id: number, n: number) => request<Script>("POST", `/api/scripts/${id}/versions/${n}/restore`),
    run: (id: number, body: ScriptQueryRun) => request<ReportResult>("POST", `/api/scripts/${id}/run`, body),
    downloadCsv: (id: number, body: ScriptQueryRun, filename: string) => download(`/api/scripts/${id}/run.csv`, body, filename),
    addExamples: () => request<string[]>("POST", "/api/scripts/examples"),
  },

  remote: {
    start: (ticketId: number, deviceId?: number) => request<RemoteLaunch>("POST", `/api/tickets/${ticketId}/remote-sessions`, { device_id: deviceId ?? null }),
    list: (ticketId: number) => request<RemoteSession[]>("GET", `/api/tickets/${ticketId}/remote-sessions`),
    close: (id: number) => request<RemoteSession>("POST", `/api/remote-sessions/${id}/close`),
  },
  runners: {
    list: () => request<Runner[]>("GET", "/api/runners"),
    create: (body: RunnerIn) => request<RunnerToken>("POST", "/api/runners", body),
    update: (id: number, body: RunnerIn) => request<Runner>("PUT", `/api/runners/${id}`, body),
    newToken: (id: number) => request<RunnerToken>("POST", `/api/runners/${id}/token`),
    remove: (id: number) => request<void>("DELETE", `/api/runners/${id}`),
  },
  runs: {
    preview: (scriptId: number, body: RunRequest) => request<RunPreview>("POST", `/api/scripts/${scriptId}/preview`, body),
    start: (scriptId: number, body: RunRequest) => request<Run[]>("POST", `/api/scripts/${scriptId}/runs`, body),
    list: (p: { script_id?: number; ticket_id?: number; status?: string; limit?: number }) => request<Run[]>("GET", `/api/runs${query(p)}`),
    get: (id: number) => request<RunDetail>("GET", `/api/runs/${id}`),
    approve: (id: number) => request<Run>("POST", `/api/runs/${id}/approve`),
    reject: (id: number) => request<Run>("POST", `/api/runs/${id}/reject`),
    cancel: (id: number) => request<Run>("POST", `/api/runs/${id}/cancel`),
  },

  scriptSchedules: {
    list: (p: { script_id?: number } = {}) => request<ScriptSchedule[]>("GET", `/api/script-schedules${query(p)}`),
    create: (body: ScriptScheduleIn) => request<ScriptSchedule>("POST", "/api/script-schedules", body),
    update: (id: number, body: ScriptScheduleIn) => request<ScriptSchedule>("PUT", `/api/script-schedules/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/script-schedules/${id}`),
    runNow: (id: number) => request<ScriptSchedule>("POST", `/api/script-schedules/${id}/run`),
  },

  ad: {
    requester: (ticketId: number) => request<S["RequesterADOut"]>("GET", `/api/tickets/${ticketId}/requester-ad`),
    groups: (q: string) => request<ADGroup[]>("GET", `/api/ad/groups${query({ q })}`),
    nesting: (dn: string) => request<ADGroup>("GET", `/api/ad/groups/nesting${query({ dn })}`),
    userGroups: (userId: number) => request<ADGroup[]>("GET", `/api/ad/users/${userId}/groups`),
    ous: (q = "") => request<S["OUOut"][]>("GET", `/api/ad/ous${query({ q })}`),
    preview: (ticketId: number, body: ADChange) => request<ADPlan>("POST", `/api/tickets/${ticketId}/ad-changes/preview`, body),
    apply: (ticketId: number, body: ADChange) => request<Run[]>("POST", `/api/tickets/${ticketId}/ad-changes`, body),
    groupLists: () => request<ADGroupList[]>("GET", "/api/ad/group-lists"),
    previewNew: (ticketId: number, body: NewADAccount) => request<NewADAccountPlan>("POST", `/api/tickets/${ticketId}/ad-account/new/preview`, body),
    createNew: (ticketId: number, body: NewADAccount) => request<NewADAccountMade>("POST", `/api/tickets/${ticketId}/ad-account/new`, body),
    templates: () => request<OnboardingTemplate[]>("GET", "/api/onboarding-templates"),
    createTemplate: (body: OnboardingTemplateIn) => request<OnboardingTemplate>("POST", "/api/onboarding-templates", body),
    updateTemplate: (id: number, body: OnboardingTemplateIn) => request<OnboardingTemplate>("PUT", `/api/onboarding-templates/${id}`, body),
    removeTemplate: (id: number) => request<void>("DELETE", `/api/onboarding-templates/${id}`),
  },
  layouts: {
    get: (page: string) => request<PageLayout>("GET", `/api/layouts/${page}`),
    saveMine: (page: string, body: Layout) => request<PageLayout>("PUT", `/api/layouts/${page}/mine`, body),
    resetMine: (page: string) => request<PageLayout>("DELETE", `/api/layouts/${page}/mine`),
    saveDefault: (page: string, body: Layout) => request<PageLayout>("PUT", `/api/layouts/${page}/default`, body),
    resetDefault: (page: string) => request<PageLayout>("DELETE", `/api/layouts/${page}/default`),
  },
  pos: {
    list: (p: { q?: string; status?: string; open_only?: boolean; vendor_id?: number; limit?: number; offset?: number } = {}) =>
      request<S["POPage"]>("GET", `/api/purchase-orders${query({ ...p, open_only: p.open_only ? "true" : undefined })}`),
    get: (id: number) => request<PO>("GET", `/api/purchase-orders/${id}`),
    create: (body: POIn) => request<PO>("POST", "/api/purchase-orders", body),
    update: (id: number, body: POIn) => request<PO>("PUT", `/api/purchase-orders/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/purchase-orders/${id}`),
    setStatus: (id: number, status: S["POStatusIn"]["status"], note = "") => request<PO>("POST", `/api/purchase-orders/${id}/status`, { status, note }),
    receive: (id: number, body: S["POReceiveIn"]) => request<PO>("POST", `/api/purchase-orders/${id}/receive`, body),
    headsUp: (id: number, body: S["HeadsUpIn"]) => request<PO>("POST", `/api/purchase-orders/${id}/heads-up`, body),
    upload: (id: number, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<S["POAttachmentOut"]>("POST", `/api/purchase-orders/${id}/attachments`, form);
    },
    removeFile: (fileId: string) => request<void>("DELETE", `/api/po-files/${fileId}`),
  },
  expenses: {
    forTicket: (ticketId: number) => request<TicketExpenses>("GET", `/api/tickets/${ticketId}/expenses`),
    add: (ticketId: number, body: ExpenseIn) => request<TicketExpenses>("POST", `/api/tickets/${ticketId}/expenses`, body),
    update: (ticketId: number, id: number, body: ExpenseIn) => request<TicketExpenses>("PUT", `/api/tickets/${ticketId}/expenses/${id}`, body),
    remove: (ticketId: number, id: number) => request<TicketExpenses>("DELETE", `/api/tickets/${ticketId}/expenses/${id}`),
  },
  receiving: {
    lookup: (po: string, code: string) => request<PublicPO>("POST", "/api/public/receiving/lookup", { po, code }),
    receive: (body: S["PublicReceiveIn"]) => request<PublicPO>("POST", "/api/public/receiving/receive", body),
    photo: (po: string, code: string, name: string, file: File) => {
      const form = new FormData();
      form.append("po", po);
      form.append("code", code);
      form.append("name", name);
      form.append("file", file);
      return request<{ ok: boolean }>("POST", "/api/public/receiving/photo", form);
    },
  },

  vendors: {
    list: (p: { q?: string } = {}) => request<Vendor[]>("GET", `/api/vendors${query(p)}`),
    create: (body: VendorIn) => request<Vendor>("POST", "/api/vendors", body),
    update: (id: number, body: VendorIn) => request<Vendor>("PUT", `/api/vendors/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/vendors/${id}`),
  },
  systems: {
    list: (p: { q?: string; vendor_id?: number } = {}) => request<SystemItem[]>("GET", `/api/systems${query(p)}`),
    create: (body: SystemIn) => request<SystemItem>("POST", "/api/systems", body),
    update: (id: number, body: SystemIn) => request<SystemItem>("PUT", `/api/systems/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/systems/${id}`),
    forTicket: (ticketId: number) => request<TicketSystem[]>("GET", `/api/tickets/${ticketId}/systems`),
    link: (ticketId: number, systemId: number) => request<TicketSystem[]>("POST", `/api/tickets/${ticketId}/systems`, { system_id: systemId }),
    unlink: (ticketId: number, systemId: number) => request<TicketSystem[]>("DELETE", `/api/tickets/${ticketId}/systems/${systemId}`),
  },
  permissions: {
    list: () => request<PermissionRule[]>("GET", "/api/permissions"),
    save: (rules: Record<string, PermissionRuleIn>) => request<PermissionRule[]>("PUT", "/api/permissions", rules),
  },
  deleteTicket: (id: number) => request<void>("DELETE", `/api/tickets/${id}`),

  surveys: {
    byLink: (token: string) => request<Survey>("GET", `/api/surveys/answer/${encodeURIComponent(token)}`),
    answerByLink: (token: string, body: SurveyAnswer) => request<Survey>("POST", `/api/surveys/answer/${encodeURIComponent(token)}`, body),
    forMyTicket: (ticketId: number) => request<Survey>("GET", `/api/portal/tickets/${ticketId}/survey`),
    answerMyTicket: (ticketId: number, body: SurveyAnswer) => request<Survey>("POST", `/api/portal/tickets/${ticketId}/survey`, body),
    results: (days = 90) => request<SurveySummary>("GET", `/api/surveys${query({ days })}`),
  },

  kb: {
    list: (p: { q?: string; category?: string; status?: string; limit?: number } = {}) => request<KbArticleSummary[]>("GET", `/api/kb/articles${query(p)}`),
    get: (id: number) => request<KbArticle>("GET", `/api/kb/articles/${id}`),
    create: (body: KbArticleIn) => request<KbArticle>("POST", "/api/kb/articles", body),
    update: (id: number, body: KbArticleIn) => request<KbArticle>("PUT", `/api/kb/articles/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/kb/articles/${id}`),
    feedback: (id: number, helpful: boolean) => request<KbArticleSummary>("POST", `/api/kb/articles/${id}/feedback`, { helpful }),
    forTicket: (ticketId: number) => request<TicketKb>("GET", `/api/tickets/${ticketId}/kb`),
    link: (ticketId: number, articleId: number) => request<TicketKb>("POST", `/api/tickets/${ticketId}/kb/links`, { article_id: articleId }),
    unlink: (ticketId: number, articleId: number) => request<TicketKb>("DELETE", `/api/tickets/${ticketId}/kb/links/${articleId}`),
    draft: (ticketId: number) => request<KbArticle>("POST", `/api/tickets/${ticketId}/kb/draft`),
    versions: (id: number) => request<S["KbVersionOut"][]>("GET", `/api/kb/articles/${id}/versions`),
    version: (id: number, n: number) => request<S["KbVersionDetailOut"]>("GET", `/api/kb/articles/${id}/versions/${n}`),
    restore: (id: number, n: number) => request<KbArticle>("POST", `/api/kb/articles/${id}/versions/${n}/restore`),
    uploadImage: (articleId: number, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<S["KbFileOut"]>("POST", `/api/kb/articles/${articleId}/files`, form);
    },
  },

  changes: {
    list: (p: { q?: string; status?: string; open_only?: boolean; starts_from?: string; starts_before?: string } = {}) =>
      request<ChangeSummary[]>("GET", `/api/changes${query({ ...p, open_only: p.open_only ? "true" : undefined })}`),
    get: (id: number) => request<Change>("GET", `/api/changes/${id}`),
    create: (body: ChangeIn) => request<Change>("POST", "/api/changes", body),
    update: (id: number, body: ChangeIn) => request<Change>("PUT", `/api/changes/${id}`, body),
    act: (id: number, action: string, note = "") => request<Change>("POST", `/api/changes/${id}/actions`, { action, note }),
    forTicket: (ticketId: number) => request<ChangeSummary[]>("GET", `/api/tickets/${ticketId}/changes`),
    templates: (includeInactive = false) => request<ChangeTemplate[]>("GET", `/api/change-templates${query({ include_inactive: includeInactive ? "true" : undefined })}`),
    createTemplate: (body: ChangeTemplateIn) => request<ChangeTemplate>("POST", "/api/change-templates", body),
    updateTemplate: (id: number, body: ChangeTemplateIn) => request<ChangeTemplate>("PUT", `/api/change-templates/${id}`, body),
    removeTemplate: (id: number) => request<void>("DELETE", `/api/change-templates/${id}`),
  },
  checklists: {
    templates: (includeInactive = false) => request<ChecklistTemplate[]>("GET", `/api/checklist-templates${query({ include_inactive: includeInactive ? "true" : undefined })}`),
    createTemplate: (body: ChecklistTemplateIn) => request<ChecklistTemplate>("POST", "/api/checklist-templates", body),
    updateTemplate: (id: number, body: ChecklistTemplateIn) => request<ChecklistTemplate>("PUT", `/api/checklist-templates/${id}`, body),
    removeTemplate: (id: number) => request<void>("DELETE", `/api/checklist-templates/${id}`),
    forTicket: (ticketId: number) => request<TicketChecklist[]>("GET", `/api/tickets/${ticketId}/checklists`),
    start: (ticketId: number, body: StartChecklist) => request<TicketChecklist>("POST", `/api/tickets/${ticketId}/checklists`, body),
    setDate: (ticketId: number, checklistId: number, keyDate: string) => request<TicketChecklist>("PUT", `/api/tickets/${ticketId}/checklists/${checklistId}/date`, { key_date: keyDate }),
  },
  junos: {
    status: () => request<S["JunosStatusOut"]>("GET", "/api/junos/status"),
    read: () => request<S["JunosReadOut"]>("POST", "/api/junos/read"),
    devices: () => request<S["VendorDeviceOut"][]>("GET", "/api/junos/devices"),
    clients: (p: { q?: string } = {}) => request<S["NetworkObservationOut"][]>("GET", `/api/junos/clients${query(p)}`),
  },
  time: {
    forTicket: (ticketId: number) => request<S["TicketTimeOut"]>("GET", `/api/tickets/${ticketId}/time`),
    log: (ticketId: number, body: S["TimeEntryIn"]) => request<S["TimeEntryOut"]>("POST", `/api/tickets/${ticketId}/time`, body),
    remove: (ticketId: number, entryId: number) => request<void>("DELETE", `/api/tickets/${ticketId}/time/${entryId}`),
  },
  ticketTemplates: {
    list: (all = false) => request<TicketTemplate[]>("GET", `/api/ticket-templates${query({ all: all ? "true" : undefined })}`),
    create: (body: S["TicketTemplateIn"]) => request<TicketTemplate>("POST", "/api/ticket-templates", body),
    update: (id: number, body: S["TicketTemplateIn"]) => request<TicketTemplate>("PUT", `/api/ticket-templates/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/ticket-templates/${id}`),
  },
  library: {
    status: () => request<S["LibraryStatus"]>("GET", "/api/library/status"),
    search: (id: number, text: string) => request<LibraryReference[]>("POST", `/api/tickets/${id}/library/search`, { query: text }),
    link: (id: number, drive_id: string, item_id: string) => request<S["LibraryLinked"]>("POST", `/api/tickets/${id}/library/link`, { drive_id, item_id, confirm: true }),
  },
  canned: {
    list: (ticketId?: number) => request<S["CannedReplyOut"][]>("GET", `/api/canned-replies${query({ ticket_id: ticketId })}`),
    create: (body: S["CannedReplyIn"]) => request<S["CannedReplyOut"]>("POST", "/api/canned-replies", body),
    update: (id: number, body: S["CannedReplyIn"]) => request<S["CannedReplyOut"]>("PUT", `/api/canned-replies/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/canned-replies/${id}`),
    render: (id: number, ticketId: number) => request<S["RenderedOut"]>("POST", `/api/canned-replies/${id}/render${query({ ticket_id: ticketId })}`),
  },
  nutanix: {
    status: () => request<S["NutanixStatusOut"]>("GET", "/api/nutanix/status"),
    test: () => request<S["NutanixTestOut"]>("POST", "/api/nutanix/test"),
    vms: (p: { q?: string; state?: string } = {}) => request<S["NutanixVmOut"][]>("GET", `/api/nutanix/vms${query(p)}`),
    forDevice: (deviceId: number) => request<S["NutanixVmOut"][]>("GET", `/api/nutanix/devices/${deviceId}/vms`),
    forTicket: (ticketId: number) => request<S["TicketVmsOut"]>("GET", `/api/nutanix/tickets/${ticketId}/vms`),
    link: (ticketId: number, extId: string) => request<void>("POST", `/api/nutanix/tickets/${ticketId}/vms`, { ext_id: extId }),
    unlink: (ticketId: number, extId: string) => request<void>("DELETE", `/api/nutanix/tickets/${ticketId}/vms/${encodeURIComponent(extId)}`),
    power: (ticketId: number, extId: string, action: S["PowerIn"]["action"]) =>
      request<S["NutanixActionOut"]>("POST", `/api/nutanix/tickets/${ticketId}/vms/${encodeURIComponent(extId)}/power`, { action }),
  },
  unifi: {
    status: () => request<S["UnifiStatusOut"]>("GET", "/api/unifi/status"),
    test: () => request<S["UnifiTestOut"]>("POST", "/api/unifi/test"),
    sites: () => request<S["NetworkSiteOut"][]>("GET", "/api/unifi/sites"),
    devices: (p: { q?: string; state?: string } = {}) => request<S["VendorDeviceOut"][]>("GET", `/api/unifi/devices${query(p)}`),
    clients: (p: { q?: string } = {}) => request<S["NetworkObservationOut"][]>("GET", `/api/unifi/clients${query(p)}`),
    voucher: (ticketId: number, body: S["VoucherIn"]) => request<S["VoucherOut"]>("POST", `/api/unifi/tickets/${ticketId}/vouchers`, body),
  },
  meraki: {
    webhooks: () => request<S["WebhookStatusOut"]>("GET", "/api/meraki/webhooks/status"),
    diagnostics: (network: string, mac: string) => request<S["MerakiDiagnosticOut"] | null>("GET", `/api/meraki/clients/${encodeURIComponent(network)}/${encodeURIComponent(mac)}/diagnostics`),
    readDiagnostics: (network: string, mac: string) => request<S["MerakiDiagnosticOut"]>("POST", `/api/meraki/clients/${encodeURIComponent(network)}/${encodeURIComponent(mac)}/diagnostics`),
    connect: () => request<S["MerakiStartOut"]>("POST", "/api/meraki/oauth/connect"),
    disconnect: () => request<S["MerakiDisconnectOut"]>("DELETE", "/api/meraki/oauth/connection"),
    status: () => request<S["MerakiStatusOut"]>("GET", "/api/meraki/status"),
    test: () => request<S["MerakiTestOut"]>("POST", "/api/meraki/test"),
    clients: (q = "") => request<S["MerakiClientOut"][]>("GET", `/api/meraki/clients${query({ q })}`),
  },
  action1: {
    software: (deviceId: number, q: string, offset = 0, ticket_id?: number) => request<S["SoftwareCatalogOut"]>("GET", `/api/action1/devices/${deviceId}/software${query({ q, offset, ticket_id })}`),
    preview: (body: S["DeploymentPreviewIn"]) => request<SoftwareDeployment>("POST", "/api/action1/deployments/preview", body),
    submit: (id: string) => request<SoftwareDeployment>("POST", `/api/action1/deployments/${id}/submit`, {}),
    refreshDeployment: (id: string) => request<SoftwareDeployment>("POST", `/api/action1/deployments/${id}/refresh`, {}),
    deployments: (deviceId: number, ticket_id?: number) => request<SoftwareDeployment[]>("GET", `/api/action1/devices/${deviceId}/deployments${query({ ticket_id })}`),
    status: () => request<Action1Status>("GET", "/api/action1/status"),
    machines: (problem?: string) => request<DevicePatch[]>("GET", `/api/action1/machines${query({ problem })}`),
    forDevice: (deviceId: number) => request<DevicePatch | null>("GET", `/api/action1/devices/${deviceId}`),
    test: () => request<S["Action1TestOut"]>("POST", "/api/action1/test"),
  },
  ipam: {
    vlans: () => request<Vlan[]>("GET", "/api/ipam/vlans"),
    createVlan: (body: VlanIn) => request<Vlan>("POST", "/api/ipam/vlans", body),
    updateVlan: (id: number, body: VlanIn) => request<Vlan>("PUT", `/api/ipam/vlans/${id}`, body),
    removeVlan: (id: number) => request<void>("DELETE", `/api/ipam/vlans/${id}`),
    subnets: (p: { q?: string; vlan_id?: number } = {}) => request<Subnet[]>("GET", `/api/ipam/subnets${query(p)}`),
    subnet: (id: number) => request<SubnetDetail>("GET", `/api/ipam/subnets/${id}`),
    nextFree: (id: number) => request<{ address: string | null }>("GET", `/api/ipam/subnets/${id}/next-free`),
    createSubnet: (body: SubnetIn) => request<Subnet>("POST", "/api/ipam/subnets", body),
    updateSubnet: (id: number, body: SubnetIn) => request<Subnet>("PUT", `/api/ipam/subnets/${id}`, body),
    removeSubnet: (id: number) => request<void>("DELETE", `/api/ipam/subnets/${id}`),
    addresses: (p: { q?: string; device_id?: number; system_id?: number } = {}) => request<IpAddress[]>("GET", `/api/ipam/addresses${query(p)}`),
    createAddress: (body: IpAddressIn) => request<IpAddress>("POST", "/api/ipam/addresses", body),
    updateAddress: (id: number, body: IpAddressIn) => request<IpAddress>("PUT", `/api/ipam/addresses/${id}`, body),
    removeAddress: (id: number) => request<void>("DELETE", `/api/ipam/addresses/${id}`),
    runners: () => request<ScanRunner[]>("GET", "/api/ipam/runners"),
    forDevice: (deviceId: number) => request<DeviceIp[]>("GET", `/api/ipam/devices/${deviceId}/addresses`),
    linkDevice: (addressId: number, deviceId: number) => request<IpAddress>("POST", `/api/ipam/addresses/${addressId}/link-device/${deviceId}`),
    scan: (subnetId: number, kind: IpScan["kind"] = "network") => request<IpScan>("POST", `/api/ipam/subnets/${subnetId}/scan?kind=${kind}`),
    scans: (subnetId: number) => request<IpScan[]>("GET", `/api/ipam/subnets/${subnetId}/scans`),
    findings: (p: { subnet_id?: number; status?: string } = {}) => request<IpFinding[]>("GET", `/api/ipam/findings${query(p)}`),
    applyFinding: (id: number) => request<IpFinding>("POST", `/api/ipam/findings/${id}/apply`),
    dismissFinding: (id: number) => request<IpFinding>("POST", `/api/ipam/findings/${id}/dismiss`),
  },

  apiKeys: {
    list: () => request<ApiKey[]>("GET", "/api/api-keys"),
    create: (name: string) => request<ApiKeyMade>("POST", "/api/api-keys", { name }),
    revoke: (id: number) => request<ApiKey>("DELETE", `/api/api-keys/${id}`),
  },

  wallboards: {
    list: () => request<Wallboard[]>("GET", "/api/wallboards"),
    create: (body: WallboardIn) => request<WallboardMade>("POST", "/api/wallboards", body),
    update: (id: number, body: WallboardIn) => request<Wallboard>("PUT", `/api/wallboards/${id}`, body),
    newLink: (id: number) => request<WallboardMade>("POST", `/api/wallboards/${id}/link`),
    revoke: (id: number) => request<Wallboard>("DELETE", `/api/wallboards/${id}`),
  },

  network: {
    devices: (p: { q?: string; subnet_id?: number } = {}) => request<NetworkDevice[]>("GET", `/api/network/devices${query(p)}`),
    device: (id: number) => request<NetworkDeviceDetail>("GET", `/api/network/devices/${id}`),
    link: (id: number, deviceId: number | null) => request<NetworkDevice>("PATCH", `/api/network/devices/${id}`, { device_id: deviceId }),
    forget: (id: number) => request<void>("DELETE", `/api/network/devices/${id}`),
    where: (mac: string) => request<SwitchPort | null>("GET", `/api/network/where${query({ mac })}`),
  },

  devices: {
    list: (p: { q?: string; include_inactive?: boolean; assigned_user_id?: number; limit?: number; offset?: number }) =>
      request<DevicePage>("GET", `/api/devices${query({ ...p, include_inactive: p.include_inactive ? "true" : undefined })}`),
    sweep: (p: { stale_days?: number; recent_days?: number } = {}) => request<Sweep>("GET", `/api/devices/sweep${query(p)}`),
    lifecycle: () => request<Lifecycle>("GET", "/api/devices/lifecycle"),
    get: (id: number) => request<DeviceDetail>("GET", `/api/devices/${id}`),
    warranty: (id: number) => request<DeviceWarranty>("GET", `/api/devices/${id}/warranty`),
    lookupWarranty: (id: number) => request<DeviceWarranty>("POST", `/api/devices/${id}/warranty/lookup`),
    applyWarranty: (id: number) => request<DeviceWarranty>("POST", `/api/devices/${id}/warranty/apply`),
    create: (body: DeviceIn) => request<Device>("POST", "/api/devices", body),
    update: (id: number, body: DeviceUpdate) => request<Device>("PATCH", `/api/devices/${id}`, body),
    remove: (id: number) => request<void>("DELETE", `/api/devices/${id}`),
    forTicket: (ticketId: number) => request<TicketDevice[]>("GET", `/api/tickets/${ticketId}/devices`),
    link: (ticketId: number, deviceId: number) => request<TicketDevice[]>("POST", `/api/tickets/${ticketId}/devices`, { device_id: deviceId }),
    unlink: (ticketId: number, deviceId: number) => request<TicketDevice[]>("DELETE", `/api/tickets/${ticketId}/devices/${deviceId}`),
  },

  updates: {
    progress: (path: string) => request<Record<string, unknown>>("GET", path),
    status: () => request<UpdateStatus>("GET", "/api/system/updates"),
    check: () => request<UpdateStatus>("POST", "/api/system/updates/check"),
    apply: (body: S["ApplyIn"]) => request<UpdateStatus>("POST", "/api/system/updates/apply", body),
  },
  system: {
    integrations: () => request<IntegrationHealth>("GET", "/api/system/integrations"),
    status: () => request<StatusItem[]>("GET", "/api/system/status"),
    logSources: () => request<LogSource[]>("GET", "/api/system/log/sources"),
    log: (p: { source?: string; level?: string; before_id?: number; limit?: number }) => request<SystemEvent[]>("GET", `/api/system/log${query(p)}`),
    services: () => request<Services>("GET", "/api/system/services"),
    runJob: (name: string) => request<Services>("POST", `/api/system/jobs/${name}/run`),
    pauseJob: (name: string, paused: boolean) => request<Services>("PUT", `/api/system/jobs/${name}`, { paused }),
    restartWorker: (force: boolean) => request<Services>("POST", "/api/system/worker/restart", { force }),
  },

  settings: {
    windowsSignIn: () => request<WindowsSignIn>("GET", "/api/settings/windows-sign-in"),
    saveWindowsSignIn: (body: { enabled: boolean; silent: boolean }) => request<WindowsSignIn>("PUT", "/api/settings/windows-sign-in", body),
    uploadKeytab: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<WindowsSignIn>("PUT", "/api/settings/windows-sign-in/keytab", form);
    },
    removeKeytab: () => request<WindowsSignIn>("DELETE", "/api/settings/windows-sign-in/keytab"),
    section: (name: string) => request<SettingsSection>("GET", `/api/settings/${name}`),
    testEmail: () => request<EmailTest>("POST", "/api/settings/email/test"),
    checkMailboxNow: () => request<MailPass>("POST", "/api/settings/email/check-now"),
    backups: () => request<BackupList>("GET", "/api/settings/backup/files"),
    verifyBackup: (name: string) => request<BackupVerify>("POST", `/api/settings/backup/files/${encodeURIComponent(name)}/verify`),
    saveSection: (name: string, values: Record<string, unknown>, clear: string[] = []) =>
      request<SettingsSection>("PUT", `/api/settings/${name}`, { values, clear }),
  },
  updateUser: (id: number, body: { role?: UserRole; is_active?: boolean; display_name?: string }) => request<User>("PATCH", `/api/users/${id}`, body),
  setPassword: (id: number, password: string) => request<void>("PUT", `/api/users/${id}/password`, { password }),
  setSignIn: (id: number, mode: "directory" | "directory_with_fallback" | "local", password?: string) =>
    request<User>("PUT", `/api/users/${id}/sign-in`, { mode, password: password || null }),

  portal: {
    queues: () => request<PortalQueue[]>("GET", "/api/portal/queues"),
    hours: () => request<PortalHours>("GET", "/api/portal/hours"),
    devices: () => request<PortalDevice[]>("GET", "/api/portal/devices"),
    create: (body: PortalTicketCreate) => request<PortalTicket>("POST", "/api/portal/tickets", body),
    tickets: (closed: boolean) => request<PortalTicketPage>("GET", `/api/portal/tickets${query({ closed: String(closed), limit: 200 })}`),
    ticket: (id: number) => request<PortalTicket>("GET", `/api/portal/tickets/${id}`),
    resolve: (id: number) => request<PortalTicket>("POST", `/api/portal/tickets/${id}/resolve`),
  },
};
