# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, computed_field, field_validator, model_validator

from app.models import AuthSource, DeviceSource, EventType, ScriptKind, ScriptLanguage, FieldList, Impact, Priority, RuleMode, RuleTrigger, TicketSource, TicketStatus, Urgency, UserRole
from app import numbering, sla
from app.previews import preview_kind
from app.reports.engine import MAX_ROWS, ReportSpec
from app.sla import SlaState


# Deliberately loose: AD shops often use internal domains like corp.local,
# which strict validators reject.
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Users


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    # The authenticator app's 6-digit code, or a recovery code, when two-factor sign-in is on.
    code: str | None = Field(default=None, max_length=32)


class TwoFactorStatusOut(BaseModel):
    enabled: bool
    recovery_codes_left: int
    required: bool


class TwoFactorSetupOut(BaseModel):
    secret: str
    uri: str


class TwoFactorConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class TwoFactorRecoveryOut(BaseModel):
    recovery_codes: list[str]


class TwoFactorOffIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=32)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class SignInModeIn(BaseModel):
    mode: Literal["directory", "directory_with_fallback", "local"]
    # Required when switching to local or first enabling the fallback.
    password: str | None = Field(default=None, max_length=1024)


class PasswordSetIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class UserCreate(BaseModel):
    email: Email
    display_name: str = Field(min_length=1, max_length=200)
    role: UserRole = UserRole.requester
    auth_source: AuthSource = AuthSource.local
    directory_id: str | None = None
    # Local accounts only. Leave out to create an account that can't sign in yet.
    password: str | None = Field(default=None, max_length=1024)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None


class UserOut(ORM):
    id: int
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    auth_source: AuthSource
    # Where the role comes from: "manual", "default" or "group:<name>".
    role_source: str
    local_fallback: bool = False
    upn: str | None = None
    department: str | None = None
    title: str | None = None
    phone: str | None = None
    last_synced_at: datetime | None = None
    password_hash: str | None = Field(default=None, exclude=True)
    # Two-factor sign-in (authenticator app) is on.
    two_factor: bool = False

    @computed_field
    @property
    def has_password(self) -> bool:
        return self.password_hash is not None


SidebarModule = Literal["dashboard", "devices", "changes", "purchase-orders", "vendors", "ipam", "kb", "scripts", "reports", "settings", "portal"]


class TicketWorkspacePreferences(BaseModel):
    """Presentation only; no ticket content or permissions are stored here."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["drawer", "classic"] = "drawer"
    selected_tool: Literal["details", "devices", "directory", "scripts", "network", "copilot", "knowledge", "files", "guest_wifi"] = "details"
    keep_open: bool = False
    width: int = Field(default=440, ge=320, le=720)


class MeOut(UserOut):
    """The signed-in user. portal_only: signed in by single sign-on, so the portal only;
    the console asks for a password. must_change_password: change it before anything else
    (the first-run admin/admin)."""

    portal_only: bool = False
    must_change_password: bool = False
    # Two-factor sign-in must be set up before anything else (Settings > Sign-in).
    must_set_up_two_factor: bool = False
    ticket_color: str | None = None
    color_rules: list["ColorRule"] = []
    # Settings > Permissions: the actions this person may take (app.permissions.CATALOG keys).
    permissions: list[str] = []
    # Optional parts in use (Settings > General): "action1".
    features: list[str] = []
    # Personal navigation only; never changes permissions, routes or global features.
    hidden_modules: list[SidebarModule] = []
    ticket_workspace: TicketWorkspacePreferences = Field(default_factory=TicketWorkspacePreferences)
    # Global console availability, above personal visibility; never an authorization grant.
    disabled_modules: list[SidebarModule] = []


HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class ColorRule(BaseModel):
    """Tint my queue's rows: the first rule a ticket matches wins."""

    field: Literal["priority", "status", "source", "queue", "requester", "sla", "assignee"]
    # priority/status/source: their codes; queue/requester: an id; sla: at_risk or breached;
    # assignee: me or unassigned.
    value: str = Field(min_length=1, max_length=100)
    color: HexColor


class PreferencesIn(BaseModel):
    ticket_color: HexColor | None = None
    color_rules: list[ColorRule] = Field(default_factory=list, max_length=30)
    # Omitted: preserve it, including when an older client saves colours. Null/[]: show all.
    hidden_modules: list[SidebarModule] | None = Field(default=None, max_length=11)
    # Omitted preserves; null resets. Explicit objects replace the presentation preferences.
    ticket_workspace: TicketWorkspacePreferences | None = None

    @field_validator("hidden_modules")
    @classmethod
    def unique_modules(cls, value):
        return sorted(set(value)) if value is not None else None


MeOut.model_rebuild()


class SignInOptionsOut(BaseModel):
    """What the sign-in page offers. Public: read before anyone is signed in."""

    windows_sign_in: bool
    # Try Windows sign-in straight away, without waiting for a click.
    windows_sign_in_silent: bool
    # Sign in with Microsoft (Entra ID) is set up.
    entra_sign_in: bool = False


class WindowsSignInOut(BaseModel):
    enabled: bool
    silent: bool
    # From the keytab.
    realm: str
    keytab_principals: list[str]
    keytab_enctypes: list[str]
    keytab_uploaded_at: datetime | None
    keytab_uploaded_by: str | None
    # Why the stored keytab can't be used right now (missing, unreadable); None when fine.
    problem: str | None = None


class SettingChoiceOut(BaseModel):
    value: str
    label: str


class SettingFieldOut(BaseModel):
    name: str
    label: str
    help: str
    # text, int, bool, list, choice, pem (a certificate kept as a file) or secret.
    # Secrets never have a value here, only is_set.
    kind: str
    choices: list[SettingChoiceOut] = []
    # Set on the server (HELPDESK_<NAME>): shown, not editable in the app.
    locked: bool
    value: Any = None
    is_set: bool | None = None


class SettingsSectionOut(BaseModel):
    section: str
    fields: list[SettingFieldOut]


class SettingsUpdateIn(BaseModel):
    # Only the fields being changed. A secret is replaced by sending its new value.
    values: dict[str, Any] = {}
    # Back to the default (a secret is deleted).
    clear: list[str] = []


class EmailTestOut(BaseModel):
    mailbox: str
    inbox_items: int
    unread: int


class MailPassItemOut(BaseModel):
    status: str  # processed, rejected or failed
    ticket_id: int | None = None
    reason: str | None = None


class MailPassOut(BaseModel):
    processed: int
    rejected: int
    failed: int
    sent: int
    items: list[MailPassItemOut]


class StatusItemOut(BaseModel):
    key: str
    label: str
    state: Literal["ok", "warning", "error", "off"]
    summary: str
    at: datetime | None = None


class SystemEventOut(ORM):
    id: int
    at: datetime
    level: Literal["info", "warning", "error"]
    source: str
    message: str
    details: str | None
    process: str


class LogSourceOut(BaseModel):
    value: str
    label: str


class JobOut(BaseModel):
    name: str
    label: str
    paused: bool
    running: bool
    run_requested: bool
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_ok: bool | None
    last_summary: str | None
    last_error: str | None
    next_due_at: datetime | None


class ApiServiceOut(BaseModel):
    host: str
    pid: int
    started_at: datetime
    version: str


class WorkerServiceOut(BaseModel):
    # running, not_responding or never_started
    state: Literal["running", "not_responding", "never_started"]
    last_seen: datetime | None
    host: str | None
    pid: int | None
    started_at: datetime | None
    restart_requested_at: datetime | None
    restart_requested_by: str | None
    # A force-restart command is set up on the server (HELPDESK_WORKER_RESTART_COMMAND).
    can_force_restart: bool


class ServicesOut(BaseModel):
    api: ApiServiceOut
    worker: WorkerServiceOut
    jobs: list[JobOut]


class JobUpdateIn(BaseModel):
    paused: bool


class WorkerRestartIn(BaseModel):
    # Run the server's restart command instead of asking the worker (for one that's stuck).
    force: bool = False


class ReportFieldOut(BaseModel):
    key: str
    label: str
    kind: str


class ReportChoiceOut(BaseModel):
    value: str
    label: str


class ReportFilterOut(BaseModel):
    key: str
    label: str
    # "enum:<name>" (values given), "queues", "agents", "options:<list>" (IDs; "none" means empty) or "text"
    choices: str
    values: list[ReportChoiceOut] = []


class ReportDateOut(BaseModel):
    key: str
    label: str


class ReportDatasetOut(BaseModel):
    name: str
    label: str
    dimensions: list[ReportFieldOut]
    measures: list[ReportFieldOut]
    filters: list[ReportFilterOut]
    dates: list[ReportDateOut]


class ReportDatasetRef(BaseModel):
    name: str
    label: str


class ReportPresetOut(BaseModel):
    key: str
    title: str
    description: str
    group: str
    spec: ReportSpec


class SavedReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    spec: ReportSpec
    # Every agent can see and run it; only its owner and admins can change it.
    shared: bool = False


class SavedReportOut(ORM):
    id: int
    name: str
    description: str
    spec: ReportSpec
    shared: bool
    owner: UserRef | None
    # The viewer may change or delete it.
    can_edit: bool = False
    updated_at: datetime


class ReportExportIn(BaseModel):
    spec: ReportSpec
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ReportEmailIn(ReportExportIn):
    recipients: list[str] = Field(min_length=1, max_length=50)
    format: Literal["pdf", "csv", "both"] = "pdf"
    message: str = Field(default="", max_length=4000)


class ReportEmailOut(BaseModel):
    queued: int


class ReportScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # A ready-made report, or a saved one.
    preset_key: str | None = None
    saved_report_id: int | None = None
    period: str = "previous_week"
    recipients: list[str] = Field(min_length=1, max_length=50)
    format: Literal["pdf", "csv", "both"] = "pdf"
    message: str = Field(default="", max_length=4000)
    frequency: Literal["once", "daily", "weekly", "monthly"] = "weekly"
    # weekly: 0 = Monday ... 6 = Sunday
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=lambda: [0])
    # monthly: 1-28, or -1 for the last day
    day_of_month: int | None = Field(default=None, ge=-1, le=28)
    time_of_day: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    start_date: date
    is_active: bool = True


class ReportScheduleOut(ORM):
    id: int
    name: str
    preset_key: str | None
    saved_report_id: int | None
    report_title: str = ""
    period: str
    recipients: list[str]
    format: str
    message: str
    frequency: str
    weekdays: list[int]
    day_of_month: int | None
    time_of_day: str
    start_date: date
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_error: str | None
    owner: UserRef | None
    can_edit: bool = False


class BackupFileOut(BaseModel):
    name: str
    size: int
    created_at: datetime


class BackupListOut(BaseModel):
    folder: str
    files: list[BackupFileOut]
    # Why the folder can't be listed (not set up, not mounted...).
    problem: str | None = None


class BackupVerifyOut(BaseModel):
    name: str
    created_at: str
    host: str
    schema_revision: str | None
    files: int
    counts: dict[str, int]


class BrandingOut(BaseModel):
    product_name: str
    sign_in_message: str
    background_in_console: bool
    portal_welcome: str
    theme_preset: Literal["custom", "midnight", "ember", "ultraviolet"] = "custom"
    theme_brand: str
    theme_sidebar: str
    dark_mode: str
    # Versioned URLs of uploaded images, or None.
    background_url: str | None
    logo_url: str | None
    favicon_url: str | None


class NoticeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=4000)
    severity: Literal["info", "warning", "outage"] = "warning"
    audience: Literal["everyone", "staff", "requesters"] = "everyone"
    # Default: now.
    starts_at: datetime | None = None
    # None: until taken down.
    ends_at: datetime | None = None


class NoticeOut(ORM):
    id: int
    title: str
    message: str
    severity: str
    audience: str
    starts_at: datetime
    ends_at: datetime | None
    created_by: UserRef | None
    updated_at: datetime
    # upcoming, current or ended
    state: str = "current"


class WindowsSignInIn(BaseModel):
    enabled: bool
    silent: bool


class UserRef(ORM):
    id: int
    email: str
    display_name: str
    # Their colour in the queue, if they chose one.
    ticket_color: str | None = None


# Queues


class QueueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    is_default: bool = False
    escalation_queue_id: int | None = None
    # Only owners, managers, viewers and admins see a restricted queue's tickets.
    restricted: bool = False
    # Aliases of the intake mailbox that file new email into this queue.
    email_addresses: list[Email] = []
    show_in_portal: bool = False
    portal_name: str | None = Field(default=None, max_length=100)
    portal_description: str | None = None
    portal_position: int = 100
    categories: list[str] = []
    category_routes: dict[str, int] = {}
    # Opening hours (app.hours lines); empty: the desk hours.
    hours: str | None = Field(default=None, max_length=2000)

    @field_validator("hours")
    @classmethod
    def valid_hours(cls, v: str | None) -> str | None:
        from app.hours import HoursError, check_week

        if v is None or not v.strip():
            return None
        try:
            check_week(v)
        except HoursError as exc:
            raise ValueError(str(exc)) from None
        return v.strip()

    @model_validator(mode="after")
    def tidy_categories(self):
        self.categories = _tidy(self.categories)
        self.category_routes = _routes(self.categories, self.category_routes)
        return self


def _tidy(values: list[str]) -> list[str]:
    """Trimmed, non-empty, no duplicates (case-insensitive), original order."""
    seen, out = set(), []
    for v in values:
        v = v.strip()
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v[:100])
    return out


def _routes(categories: list[str], routes: dict[str, int]) -> dict[str, int]:
    """Keep only routes for options that exist, keyed by the option's own spelling."""
    by_key = {c.casefold(): c for c in categories}
    return {by_key[k.strip().casefold()]: v for k, v in routes.items() if k.strip().casefold() in by_key and v}


class QueueUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_default: bool | None = None
    escalation_queue_id: int | None = None
    is_active: bool | None = None
    # Only owners, managers, viewers and admins see a restricted queue's tickets.
    restricted: bool | None = None
    # Aliases of the intake mailbox that file new email into this queue.
    email_addresses: list[Email] | None = None
    show_in_portal: bool | None = None
    portal_name: str | None = Field(default=None, max_length=100)
    portal_description: str | None = None
    portal_position: int | None = None
    categories: list[str] | None = None
    category_routes: dict[str, int] | None = None
    # Opening hours (app.hours lines); empty: the desk hours.
    hours: str | None = Field(default=None, max_length=2000)

    @field_validator("hours")
    @classmethod
    def valid_hours(cls, v: str | None) -> str | None:
        from app.hours import HoursError, check_week

        if v is None or not v.strip():
            return None
        try:
            check_week(v)
        except HoursError as exc:
            raise ValueError(str(exc)) from None
        return v.strip()

    @model_validator(mode="after")
    def tidy_categories(self):
        if self.categories is not None:
            self.categories = _tidy(self.categories)
        if self.category_routes is not None and self.categories is not None:
            self.category_routes = _routes(self.categories, self.category_routes)
        return self


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    source: AuthSource = AuthSource.local
    # Directory groups: the AD objectGUID or Entra group object ID.
    directory_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def directory_needs_id(self):
        if self.source == AuthSource.directory and not self.directory_id:
            raise ValueError("Directory groups need a directory_id")
        return self


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    is_active: bool | None = None


class GroupRef(ORM):
    id: int
    name: str
    source: AuthSource


class GroupOut(ORM):
    id: int
    name: str
    description: str | None
    source: AuthSource
    directory_id: str | None
    is_active: bool
    members: list[UserRef]


class MembersIn(BaseModel):
    user_ids: list[int]


class QueuePeopleIn(BaseModel):
    """Owners or managers of a queue: any mix of users and groups. Replaces the current set."""

    user_ids: list[int] = []
    group_ids: list[int] = []


class QueueOut(ORM):
    id: int
    name: str
    description: str | None
    is_default: bool
    escalation_queue_id: int | None
    is_active: bool
    show_in_portal: bool
    portal_name: str | None
    portal_description: str | None
    portal_position: int
    categories: list[str]
    category_routes: dict[str, int]
    restricted: bool
    hours: str | None
    email_addresses: list[str]
    owner_users: list[UserRef]
    owner_groups: list[GroupRef]
    manager_users: list[UserRef]
    manager_groups: list[GroupRef]
    viewer_users: list[UserRef]
    viewer_groups: list[GroupRef]


class QueueRef(ORM):
    id: int
    name: str


# SLA policies


class SlaPolicyIn(BaseModel):
    first_response_minutes: int = Field(gt=0)
    resolution_minutes: int = Field(gt=0)
    at_risk_percent: int = Field(default=75, ge=1, le=99)

    @model_validator(mode="after")
    def response_before_resolution(self):
        if self.first_response_minutes > self.resolution_minutes:
            raise ValueError("first_response_minutes cannot exceed resolution_minutes")
        return self


class SlaPolicyOut(ORM):
    priority: Priority
    first_response_minutes: int
    resolution_minutes: int
    at_risk_percent: int


# Routing rules


class RoutingRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    position: int = 100
    is_active: bool = True
    match_category: str | None = None
    match_keywords: list[str] | None = None
    match_requester: str | None = Field(default=None, description='Exact email, or a domain like "@corp.local"')
    match_source: TicketSource | None = None
    match_priority: Priority | None = None
    match_requested_queue_id: int | None = Field(default=None, description="The queue the requester chose in the portal")
    override_requested_queue: bool = Field(default=False, description="May replace a requester's chosen queue")
    set_queue_id: int | None = None
    set_priority: Priority | None = None

    @model_validator(mode="after")
    def has_action(self):
        if self.set_queue_id is None and self.set_priority is None:
            raise ValueError("A rule must set a queue, a priority, or both")
        if self.match_keywords is not None:
            self.match_keywords = [k.strip() for k in self.match_keywords if k.strip()] or None
        return self


class RoutingRuleOut(ORM):
    id: int
    name: str
    position: int
    is_active: bool
    match_category: str | None
    match_keywords: list[str] | None
    match_requester: str | None
    match_source: TicketSource | None
    match_priority: Priority | None
    match_requested_queue_id: int | None
    override_requested_queue: bool
    set_queue_id: int | None
    set_priority: Priority | None


# Tickets


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    description: str = ""
    category: str | None = Field(default=None, max_length=100)
    # Leave out to derive it from impact and urgency (or medium without them).
    priority: Priority | None = None
    urgency: Urgency | None = None
    impact: Impact | None = None
    # File a ticket on someone's behalf (pick them, or give their email), and say where it came from.
    requester_id: int | None = None
    requester_email: Email | None = None
    # Default to the requester's, from AD.
    contact_phone: str | None = Field(default=None, max_length=64)
    department_id: int | None = None
    # The requester's computer; empty: their one assigned device, if they have exactly one.
    computer_id: int | None = None
    # Make it a child of this ticket. Without a requester, it takes the parent's.
    parent_id: int | None = None
    source: TicketSource | None = None
    # Put it straight in this queue (treated like a requester's choice for routing).
    queue_id: int | None = None


class ComputerRef(ORM):
    id: int
    name: str
    dns_hostname: str | None
    os: str | None
    last_logon_at: datetime | None
    is_active: bool
    assigned_user_id: int | None


class Labelled(BaseModel):
    """Adds the ticket's label (Settings > General), e.g. #1042 or HD-1042."""

    @computed_field
    @property
    def label(self) -> str:
        return numbering.label(self.id, getattr(self, "created_at", None))  # type: ignore[attr-defined]


class TicketBrief(ORM, Labelled):
    id: int
    subject: str
    status: TicketStatus
    created_at: datetime | None = None


class ChildTicketOut(ORM, Labelled):
    id: int
    subject: str
    status: TicketStatus
    priority: Priority
    queue: "QueueRef"
    assignee: UserRef | None
    created_at: datetime


class MergeIn(BaseModel):
    into_id: int
    # A Settings > Ticket fields merge reason.
    reason_id: int
    note: str = Field(default="", max_length=2000)


class TicketUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    status: TicketStatus | None = None
    priority: Priority | None = None
    # Changing these re-prioritises from the matrix unless priority is also given.
    urgency: Urgency | None = None
    impact: Impact | None = None
    # Settings > Ticket fields choices (null clears). Resolving or closing may require the first two.
    resolution_type_id: int | None = None
    closure_reason_id: int | None = None
    department_id: int | None = None
    contact_phone: str | None = Field(default=None, max_length=64)
    # The requester's computer (a device id; null clears).
    computer_id: int | None = None
    # Part of this ticket (null: of none).
    parent_id: int | None = None


class AssignIn(BaseModel):
    queue_id: int | None = None
    assignee_id: int | None = None
    unassign: bool = False
    # Added to the internal "moved from X to Y" note when the queue changes.
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def something_to_do(self):
        if self.unassign and self.assignee_id is not None:
            raise ValueError("Pass assignee_id or unassign, not both")
        if self.queue_id is None and self.assignee_id is None and not self.unassign:
            raise ValueError("Nothing to change")
        return self


class EscalateIn(BaseModel):
    reason: str = Field(min_length=1)


class FieldOptionRef(ORM):
    id: int
    label: str


class FieldOptionOut(ORM):
    id: int
    list: FieldList
    label: str
    position: int
    is_active: bool
    # Built-in choices the app uses itself (can be renamed, not removed).
    key: str | None


class FieldOptionIn(BaseModel):
    list: FieldList
    label: str = Field(min_length=1, max_length=100)


class FieldOptionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class TicketOut(ORM, Labelled):
    id: int
    subject: str
    description: str
    category: str | None
    source: TicketSource
    status: TicketStatus
    priority: Priority
    urgency: Urgency | None
    impact: Impact | None
    requester: UserRef
    queue: QueueRef
    requested_queue_id: int | None
    assignee: UserRef | None
    resolution_type: FieldOptionRef | None = None
    closure_reason: FieldOptionRef | None = None
    department: FieldOptionRef | None = None
    contact_phone: str | None = None
    # Fields an import (Track-It!) brought that Layer 8 has no field for; shown on this ticket only.
    imported_fields: dict[str, str] = {}
    computer: "ComputerRef | None" = None
    parent: "TicketBrief | None" = None
    merged_into: "TicketBrief | None" = None
    child_count: int = 0
    open_child_count: int = 0
    matched_rule_id: int | None
    matched_rule_name: str | None
    first_response_due_at: datetime | None
    resolution_due_at: datetime | None
    first_responded_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    sla_at_risk_at: datetime | None
    sla_paused_at: datetime | None = None
    # Business minutes left on the resolution deadline (negative: past it), and the policy's window.
    sla_minutes_left: int | None = None
    sla_resolution_minutes: int | None = None
    attachment_count: int = 0
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def sla_state(self) -> SlaState:
        return sla.sla_state(self, sla.utcnow())


class AttachmentOut(ORM):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    comment_id: int | None
    uploaded_by_id: int | None
    uploaded_by: UserRef | None
    created_at: datetime

    @computed_field
    @property
    def preview(self) -> str | None:
        """How the browser can show it inline: "image", "pdf" or "text". None means download only."""
        return preview_kind(self.content_type, self.filename)


class UnacceptedOut(BaseModel):
    enabled: bool
    count: int
    minutes: int


class TicketPage(BaseModel):
    items: list[TicketOut]
    total: int
    limit: int
    offset: int


class SavedView(StrEnum):
    open = "open"
    unassigned = "unassigned"
    mine = "mine"
    my_queues = "my_queues"
    sla_at_risk = "sla_at_risk"
    waiting_on_user = "waiting_on_user"
    from_email = "from_email"
    from_portal = "from_portal"


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    # Internal: technicians and admins only. Otherwise it's shown to the requester in the portal...
    is_internal: bool = False
    # ...and, from staff, emailed to them too unless this is false.
    email_requester: bool = True


class CommentOut(ORM):
    id: int
    author: UserRef
    body: str
    is_internal: bool
    emailed: bool = False
    source: TicketSource
    created_at: datetime


class EventOut(ORM):
    id: int
    type: EventType
    actor: UserRef | None
    data: dict
    created_at: datetime


# Self-service portal. Requesters see a slimmer ticket: no routing, SLA internals or history.


class PortalPersonOut(BaseModel):
    """A technician and their Teams status: available, busy, away, offline or unknown. Status only."""

    name: str
    status: str


class PortalHoursOut(BaseModel):
    lines: list[str]
    open_now: bool
    closes_at: datetime | None
    next_open_at: datetime | None
    # Closed today for a named closed day, e.g. "Christmas Day".
    closed_for: str | None
    # The after-hours message, filled in, when closed.
    message: str | None


class PortalQueueOut(BaseModel):
    id: int
    name: str
    description: str | None
    categories: list[str]
    hours: PortalHoursOut
    # None when Teams status isn't shown (Settings > Desk hours).
    technicians: list[PortalPersonOut] | None = None


class PortalTicketCreate(BaseModel):
    # Optional: with no portal queues configured, routing picks the queue as for email.
    queue_id: int | None = None
    category: str | None = Field(default=None, max_length=100)
    subject: str = Field(min_length=1, max_length=300)
    description: str = ""
    # "How urgent is it?" and "Who is affected?": together they set the priority.
    urgency: Urgency
    impact: Impact
    # "Which computer?": one of the requester's own devices (GET /api/portal/devices).
    computer_id: int | None = None


class PortalDeviceOut(BaseModel):
    id: int
    name: str
    os: str | None


class PortalTicketOut(Labelled):
    id: int
    subject: str
    description: str
    category: str | None
    status: TicketStatus
    urgency: Urgency | None
    impact: Impact | None
    queue_name: str
    assignee_name: str | None
    attachment_count: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None


class PortalTicketPage(BaseModel):
    items: list[PortalTicketOut]
    total: int
    limit: int
    offset: int


class RequesterChangeIn(BaseModel):
    # Pick an existing user...
    user_id: int | None = None
    # ...or link by email.
    email: Email | None = None
    # Used only when no account has this email yet; a requester account is created.
    display_name: str | None = Field(default=None, max_length=200)


# Custom ticket rules


class TicketRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    is_active: bool = True
    position: int = 100
    trigger: RuleTrigger
    schedule_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 31)
    mode: RuleMode = RuleMode.builder
    conditions: list[dict] = []
    select_sql: str | None = None
    actions: list[dict] = []
    update_sql: str | None = None
    max_rows: int = Field(default=100, ge=1, le=10_000)
    # Empty: every queue.
    queue_ids: list[int] = []


class TicketRuleOut(ORM):
    id: int
    name: str
    description: str | None
    is_active: bool
    position: int
    trigger: RuleTrigger
    schedule_minutes: int | None
    mode: RuleMode
    conditions: list[dict]
    select_sql: str | None
    actions: list[dict]
    update_sql: str | None
    max_rows: int
    queues: list[QueueRef]
    next_run_at: datetime | None
    last_run_at: datetime | None


class RuleRunOut(ORM):
    id: int
    trigger: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime | None
    matched: int
    changed: int
    emails: int
    error: str | None
    details: list[dict]


class RuleCatalogOut(BaseModel):
    """What the rule builder offers: condition fields and their operators, action types, template variables."""

    ticket_fields: dict[str, dict]
    change_fields: dict[str, dict]
    actions: dict[str, dict]
    template_vars: list[str]
    sql_enabled: bool


# Directory (AD / LDAP)


class DirectorySyncRunOut(ORM):
    id: int
    started_at: datetime
    finished_at: datetime | None
    dry_run: bool
    users_seen: int
    created: int
    updated: int
    linked: int
    deactivated: int
    role_changes: int
    groups_synced: int
    devices_seen: int = 0
    error: str | None
    details: list[dict]


class DirectoryStatusOut(BaseModel):
    configured: bool
    kind: str
    servers: list[str]
    base_dn: str
    sync_minutes: int
    last_run: DirectorySyncRunOut | None
    tracked_groups: int
    directory_users: int


class DirectoryGroupOut(BaseModel):
    guid: str
    dn: str
    name: str
    description: str | None
    # Set when this AD group is already imported.
    group_id: int | None


class DirectoryLookupIn(BaseModel):
    login: str = Field(min_length=1, max_length=320)


class DirectoryLookupOut(BaseModel):
    found: bool
    guid: str | None = None
    dn: str | None = None
    email: str | None = None
    display_name: str | None = None
    enabled: bool | None = None
    upn: str | None = None
    sam: str | None = None
    aliases: list[str] = []
    # Imported groups they're in (nested), and the role the mappings would give them.
    groups: list[str] = []
    role: UserRole | None = None
    role_source: str | None = None


class ImportGroupIn(BaseModel):
    guid: str = Field(min_length=1, max_length=64)


class RoleMappingIn(BaseModel):
    group_id: int
    role: UserRole

    @model_validator(mode="after")
    def not_requester(self):
        if self.role == UserRole.requester:
            raise ValueError("Map groups to agent or admin; everyone else is a requester")
        return self


class RoleMappingOut(BaseModel):
    group: GroupRef
    role: UserRole
    member_count: int
    last_synced_at: datetime | None
    sync_error: str | None


class RolePreviewOut(BaseModel):
    """What saving these mappings would change."""

    error: str | None
    changes: list[dict]
    admins: int
    agents: int


class PriorityCell(BaseModel):
    impact: Impact
    urgency: Urgency
    priority: Priority


# Devices


class DeviceOut(ORM):
    id: int
    name: str
    dns_hostname: str | None
    source: DeviceSource
    directory_dn: str | None
    os: str | None
    os_version: str | None
    description: str | None
    enabled: bool
    is_active: bool
    last_logon_at: datetime | None
    assigned_user: UserRef | None
    notes: str
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    purchased_on: date | None = None
    warranty_ends: date | None = None
    replace_by: date | None = None
    created_at: datetime
    updated_at: datetime


class DevicePage(BaseModel):
    items: list[DeviceOut]
    total: int


class DeviceTicketOut(Labelled):
    id: int
    subject: str
    status: TicketStatus
    created_at: datetime
    linked_at: datetime


class DeviceDetailOut(DeviceOut):
    # Linked tickets this person can see, newest first.
    tickets: list[DeviceTicketOut] = []


class DeviceIn(BaseModel):
    """A device added by hand. For AD computers only notes and the assigned user can change (sync owns the rest)."""

    name: str = Field(min_length=1, max_length=255)
    dns_hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=255)
    os_version: str | None = Field(default=None, max_length=100)
    description: str | None = None
    assigned_user_id: int | None = None
    notes: str = ""


class DeviceUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    dns_hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=255)
    os_version: str | None = Field(default=None, max_length=100)
    description: str | None = None
    assigned_user_id: int | None = None
    notes: str | None = None
    # Lifecycle: technicians may change these (and notes), on AD computers too.
    manufacturer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    serial: str | None = Field(default=None, max_length=200)
    purchased_on: date | None = None
    warranty_ends: date | None = None
    replace_by: date | None = None


class TicketDeviceOut(BaseModel):
    device: DeviceOut
    linked_at: datetime
    linked_by: UserRef | None


class TicketDeviceIn(BaseModel):
    device_id: int


# Script library


class ScriptParam(BaseModel):
    name: str
    label: str = ""
    type: Literal["text", "int", "bool", "date", "choice"] = "text"
    required: bool = False
    default: Any = None
    choices: list[str] = []


class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: ScriptKind
    language: ScriptLanguage
    high_risk: bool = False
    agents_can_run: bool = False
    is_active: bool = True
    body: str = Field(min_length=1, max_length=200_000)
    parameters: list[ScriptParam] = []
    # Why this version (kept with it).
    note: str = Field(default="", max_length=2000)


class ScriptOut(BaseModel):
    id: int
    name: str
    description: str
    kind: ScriptKind
    language: ScriptLanguage
    high_risk: bool
    agents_can_run: bool
    is_active: bool
    current_version: int
    body: str
    parameters: list[ScriptParam]
    updated_at: datetime
    updated_by: UserRef | None
    # Whether this person may run it / edit it.
    can_run: bool
    can_edit: bool


class ScriptVersionOut(BaseModel):
    number: int
    note: str
    author: UserRef | None
    created_at: datetime
    is_current: bool


class ScriptVersionDetailOut(ScriptVersionOut):
    language: ScriptLanguage | None = None
    reviewed_by: UserRef | None = None
    reviewed_at: datetime | None = None
    body: str
    parameters: list[ScriptParam]
    # Unified diff against the version before it ("" for the first).
    diff: str


class ScriptQueryRunIn(BaseModel):
    values: dict[str, Any] = {}
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=500, ge=1, le=MAX_ROWS)


# Runners and runs

RUNNER_LANGUAGES = Literal["powershell", "bash", "python", "eos"]


class RunnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    languages: list[RUNNER_LANGUAGES] = Field(min_length=1)
    is_active: bool = True
    builtins_only: bool = False


class RunnerOut(ORM):
    id: int
    name: str
    description: str
    languages: list[str]
    is_active: bool
    builtins_only: bool = False
    last_seen_at: datetime | None
    last_host: str | None
    last_version: str | None
    created_at: datetime


class RunnerTokenOut(BaseModel):
    runner: RunnerOut
    # Shown once: the runner's config needs it.
    token: str


class RunRequestIn(BaseModel):
    device_ids: list[int] = []
    # A query whose result has a device_id column: run on those devices.
    group_script_id: int | None = None
    # Run once on the runner itself (e.g. AD tasks) instead of on devices.
    on_runner: bool = False
    values: dict[str, Any] = {}
    ticket_id: int | None = None


class RunTargetOut(BaseModel):
    device_id: int | None
    name: str
    target: str | None


class RunPreviewOut(BaseModel):
    targets: list[RunTargetOut]
    values: dict[str, Any]
    needs_approval: bool
    # Active runners that take this language, and whether any was seen in the last few minutes.
    runners: list[str]
    runner_online: bool


class RunOut(BaseModel):
    id: int
    script_id: int
    script_name: str
    version: int
    language: str
    device_id: int | None
    target: str | None
    values: dict[str, Any]
    ticket_id: int | None
    status: str
    requested_by: UserRef | None
    decided_by: UserRef | None
    runner_name: str | None
    created_at: datetime
    # Waits until then before a runner takes it.
    not_before: datetime | None = None
    claimed_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    error: str | None
    can_decide: bool = False
    can_cancel: bool = False
    # Queued by this script schedule.
    schedule_id: int | None = None


class ScriptScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    script_id: int
    values: dict[str, Any] = {}
    # An action's targets: devices, a dynamic group (worked out at each run), or the runner itself.
    device_ids: list[int] = Field(default_factory=list, max_length=200)
    group_script_id: int | None = None
    on_runner: bool = False
    # A query's period (:start/:end; empty: none) and who gets its CSV.
    period: str = ""
    recipients: list[str] = Field(default_factory=list, max_length=50)
    message: str = Field(default="", max_length=4000)
    frequency: Literal["once", "daily", "weekly", "monthly"] = "weekly"
    # weekly: 0 = Monday ... 6 = Sunday
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=lambda: [0])
    # monthly: 1-28, or -1 for the last day
    day_of_month: int | None = Field(default=None, ge=-1, le=28)
    time_of_day: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    start_date: date
    is_active: bool = True


class ScriptScheduleOut(ORM):
    id: int
    name: str
    script_id: int
    script_name: str = ""
    script_kind: Literal["query", "action"] = "action"
    owner: UserRef | None
    values: dict[str, Any]
    device_ids: list[int]
    devices: list["SystemDeviceRef"] = []
    group_script_id: int | None
    group_name: str | None = None
    on_runner: bool
    period: str
    recipients: list[str]
    message: str
    frequency: str
    weekdays: list[int]
    day_of_month: int | None
    time_of_day: str
    start_date: date
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_result: str
    last_error: str | None
    can_edit: bool = False
    # The latest runs it queued (actions).
    recent_runs: list["RunOut"] = []


class RunDetailOut(RunOut):
    output: str
    body: str


class RunnerClaimIn(BaseModel):
    host: str = ""
    version: str = ""


class RunnerJobOut(BaseModel):
    run_id: int
    language: str
    body: str
    values: dict[str, Any]
    # Host name to run on; None: on the runner itself.
    target: str | None
    target_os: str | None
    time_limit_seconds: int
    # Given to this run once (e.g. a new account's first password): PowerShell gets them as
    # variables like the values, but they never go in the environment, the log or the output.
    secret_values: dict[str, Any] = {}


class RunnerOutputIn(BaseModel):
    text: str = Field(max_length=200_000)


class RunnerFinishIn(BaseModel):
    exit_code: int | None = None
    error: str | None = None


# Remote sessions


class RemoteSessionIn(BaseModel):
    # Default: the ticket's computer.
    device_id: int | None = None


class RemoteSessionOut(BaseModel):
    id: int
    ticket_id: int
    device_id: int | None
    target: str
    tool: str
    started_by: UserRef | None
    started_at: datetime
    ended_at: datetime | None


class RemoteLaunchOut(BaseModel):
    session: RemoteSessionOut
    # Opens the launcher on the technician's PC (docs/remote-assistance.md); used once.
    launch_uri: str


class TicketScope(StrEnum):
    """Whose tickets the queue shows; the saved views filter within it."""

    mine = "mine"  # assigned to me
    groups = "groups"  # in queues I own, directly or through a group
    all = "all"  # everything I may see


class BulkAssignIn(BaseModel):
    ticket_ids: list[int] = Field(min_length=1, max_length=200)
    queue_id: int | None = None
    assignee_id: int | None = None
    unassign: bool = False
    note: str | None = Field(default=None, max_length=2000)


class BulkResultOut(BaseModel):
    done: list[int]
    # ticket id -> why it wasn't changed
    failed: dict[int, str]


# Vendors and systems

CONTACT_ROLES = Literal["support", "sales", "account manager", "escalation", "other"]
ADDRESS_KINDS = Literal["management", "vip", "ilo", "service", "other"]


class VendorContactIn(BaseModel):
    id: int | None = None  # existing contact (kept), or new
    name: str = Field(min_length=1, max_length=200)
    role: CONTACT_ROLES = "support"
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=64)
    mobile: str = Field(default="", max_length=64)
    notes: str = ""


class VendorContactOut(ORM):
    id: int
    name: str
    role: str
    email: str
    phone: str
    mobile: str
    notes: str


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_number: str = Field(default="", max_length=100)
    website: str = Field(default="", max_length=500)
    support_phone: str = Field(default="", max_length=64)
    support_email: str = Field(default="", max_length=320)
    support_portal: str = Field(default="", max_length=500)
    support_hours: str = Field(default="", max_length=200)
    contract_level: str = Field(default="", max_length=200)
    contract_ends: date | None = None
    notes: str = ""
    is_active: bool = True
    # The full list: contacts left out are removed.
    contacts: list[VendorContactIn] = Field(default_factory=list, max_length=50)


class VendorRef(ORM):
    id: int
    name: str
    support_phone: str
    support_email: str
    support_portal: str
    support_hours: str
    account_number: str


class VendorOut(ORM):
    id: int
    name: str
    account_number: str
    website: str
    support_phone: str
    support_email: str
    support_portal: str
    support_hours: str
    contract_level: str
    contract_ends: date | None
    notes: str
    is_active: bool
    contacts: list[VendorContactOut]
    updated_at: datetime
    system_count: int = 0


class SystemAddressIn(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    kind: ADDRESS_KINDS = "management"
    notes: str = Field(default="", max_length=500)


class SystemAddressOut(ORM):
    id: int
    address: str
    kind: str
    notes: str


class SystemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    vendor_id: int | None = None
    contact_id: int | None = None
    notes: str = ""
    is_active: bool = True
    device_ids: list[int] = Field(default_factory=list, max_length=500)
    addresses: list[SystemAddressIn] = Field(default_factory=list, max_length=200)


class SystemDeviceRef(ORM):
    id: int
    name: str
    dns_hostname: str | None
    os: str | None


class SystemOut(ORM):
    id: int
    name: str
    description: str
    vendor: VendorRef | None
    contact: VendorContactOut | None
    notes: str
    is_active: bool
    devices: list[SystemDeviceRef]
    addresses: list[SystemAddressOut]
    updated_at: datetime


class TicketSystemOut(BaseModel):
    system: SystemOut
    linked_at: datetime
    linked_by: UserRef | None


class TicketSystemIn(BaseModel):
    system_id: int


# Purchase orders and expenses


class POLineIn(BaseModel):
    id: int | None = None  # an existing line (keeps what was received), or new
    item: str = Field(min_length=1, max_length=300)
    description: str = ""
    part_number: str = Field(default="", max_length=100)
    quantity: float = Field(default=1, gt=0, le=1_000_000)
    unit_price: float = Field(default=0, ge=0, le=100_000_000)
    tax_rate: float = Field(default=0, ge=0, le=100)


class POLineOut(BaseModel):
    id: int
    item: str
    description: str
    part_number: str
    quantity: float
    unit_price: float
    tax_rate: float
    received_quantity: float
    line_total: float


class POIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    vendor_id: int | None = None
    department_id: int | None = None
    requested_by_id: int | None = None
    approver_id: int | None = None
    order_date: date | None = None
    expected_date: date | None = None
    ship_to: str = ""
    budget_code: str = Field(default="", max_length=100)
    vendor_reference: str = Field(default="", max_length=200)
    shipping: float = Field(default=0, ge=0, le=100_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    terms: str = ""
    notes: str = ""
    # The full lists: lines left out are removed (a line with something received can't be).
    lines: list[POLineIn] = Field(default_factory=list, max_length=500)
    ticket_ids: list[int] = Field(default_factory=list, max_length=200)
    device_ids: list[int] = Field(default_factory=list, max_length=200)


class POStatusIn(BaseModel):
    status: Literal["draft", "requested", "approved", "ordered", "cancelled", "closed"]
    note: str = Field(default="", max_length=2000)


class POReceiveLine(BaseModel):
    line_id: int
    # How many arrived now (added to what was received before; negative corrects a mistake).
    quantity: float = Field(ge=-1_000_000, le=1_000_000)


class POReceiveIn(BaseModel):
    lines: list[POReceiveLine] = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)


class POAttachmentOut(ORM):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by: UserRef | None
    created_at: datetime


class POEventOut(ORM):
    id: int
    type: str
    data: dict
    actor: UserRef | None
    created_at: datetime


class POTicketRef(ORM, Labelled):
    id: int
    subject: str
    status: TicketStatus
    created_at: datetime | None = None


class POSummaryOut(BaseModel):
    id: int
    label: str
    title: str
    vendor: VendorRef | None
    department: FieldOptionRef | None
    requested_by: UserRef | None
    approver: UserRef | None
    status: str
    order_date: date | None
    expected_date: date | None
    currency: str
    total: float
    lines_count: int
    lines_received: int
    created_at: datetime
    updated_at: datetime


class PODetailOut(POSummaryOut):
    ship_to: str
    budget_code: str
    vendor_reference: str
    shipping: float
    terms: str
    notes: str
    approved_at: datetime | None
    subtotal: float
    tax: float
    lines: list[POLineOut]
    tickets: list[POTicketRef]
    devices: list[SystemDeviceRef]
    attachments: list[POAttachmentOut]
    events: list[POEventOut]
    can_approve: bool
    receiving_code: str | None
    # The public receiving page with this PO filled in (None when that page is off).
    receiving_url: str | None = None


class POPage(BaseModel):
    items: list[POSummaryOut]
    total: int


class ExpenseIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    vendor_id: int | None = None
    quantity: float = Field(default=1, gt=0, le=1_000_000)
    unit_cost: float = Field(default=0, ge=0, le=100_000_000)
    po_id: int | None = None
    notes: str = ""


class ExpenseOut(BaseModel):
    id: int
    description: str
    vendor: VendorRef | None
    quantity: float
    unit_cost: float
    total: float
    po_id: int | None
    po_label: str | None
    notes: str
    created_by: UserRef | None
    created_at: datetime


class TicketExpensesOut(BaseModel):
    expenses: list[ExpenseOut]
    # Purchase orders linked to the ticket, whether or not an expense points at them.
    purchase_orders: list[POSummaryOut]
    total: float


class PublicPOIn(BaseModel):
    po: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=16)


class PublicPOLine(BaseModel):
    id: int
    item: str
    part_number: str
    quantity: float
    received_quantity: float


class PublicPOOut(BaseModel):
    """What the receiving clerk sees: what's expected, never prices."""

    label: str
    title: str
    vendor: str | None
    expected_date: date | None
    ship_to: str
    status: str
    lines: list[PublicPOLine]


class PublicReceiveIn(PublicPOIn):
    name: str = Field(min_length=1, max_length=200)
    lines: list[POReceiveLine] = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)


class HeadsUpIn(BaseModel):
    # Default: receiving's address (Settings > Purchasing).
    to: Email | None = None
    message: str = Field(default="", max_length=2000)


# AD changes from tickets, onboarding templates


class ADGroupOut(BaseModel):
    dn: str
    name: str
    changeable: bool = False
    nested: bool = False
    warning: str | None = None


class RequesterADOut(BaseModel):
    enabled: bool
    # New accounts can be made from tickets (Settings > Directory: where new accounts go).
    can_create: bool = False
    # Why the card can't be used (no directory, local account...), else None.
    problem: str | None = None
    name: str | None = None
    dn: str | None = None
    ou: str | None = None
    groups: list[ADGroupOut] = []


class ADChangeIn(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=100)
    remove: list[str] = Field(default_factory=list, max_length=100)
    move_to_ou: str | None = Field(default=None, max_length=1000)
    department: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    template_id: int | None = None


class ADPlanOut(BaseModel):
    add: list[ADGroupOut]
    remove: list[ADGroupOut]
    already: list[ADGroupOut]
    move_to_ou: str | None
    current_ou: str
    department: str | None
    title: str | None


class ADGroupListOut(BaseModel):
    label: str
    prefix: str
    groups: list[ADGroupOut]


class NewADAccountIn(BaseModel):
    given_name: str = Field(max_length=64)
    surname: str = Field(max_length=64)
    display_name: str = Field(default="", max_length=256)
    # Empty: made from Settings > Directory's name format (with a number added if it's taken).
    sam: str = Field(default="", max_length=20)
    mail: str = Field(default="", max_length=256)
    ou: str = Field(default="", max_length=1000)
    phone: str = Field(default="", max_length=64)
    # Stored in ipPhone.
    extension: str = Field(default="", max_length=64)
    mobile: str = Field(default="", max_length=64)
    department: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=128)
    office: str = Field(default="", max_length=128)
    company: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=1024)
    manager_id: int | None = None
    groups: list[str] = Field(default_factory=list, max_length=200)
    template_id: int | None = None
    # A future start date: created disabled, and enabled on that day by a run that waits for it.
    start_date: date | None = None
    # Email the manager when it's created: account name, sign-in and email, never the password.
    email_manager: bool = False


class NewADAccountPlanOut(BaseModel):
    given_name: str
    surname: str
    display_name: str
    sam: str
    upn: str
    mail: str
    ou: str
    phone: str
    extension: str
    mobile: str
    department: str
    title: str
    office: str
    company: str
    description: str
    manager: str | None
    groups: list[ADGroupOut]
    enabled: bool
    change_password: bool
    start_date: date | None
    email_manager: bool
    # Anything here stops it being created.
    problems: list[str]


class NewADAccountOut(BaseModel):
    runs: list["RunOut"]
    sam: str
    upn: str
    # Shown once, here; Layer 8 keeps no copy.
    password: str


class OUOut(BaseModel):
    dn: str
    name: str


class TemplateGroup(BaseModel):
    dn: str = Field(min_length=3, max_length=1000)
    name: str = Field(default="", max_length=300)


class OnboardingTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    groups: list[TemplateGroup] = Field(default_factory=list, max_length=100)
    ou_dn: str = Field(default="", max_length=1000)
    department: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    is_active: bool = True


class OnboardingTemplateOut(ORM):
    id: int
    name: str
    description: str
    groups: list[TemplateGroup]
    ou_dn: str
    department: str
    title: str
    is_active: bool
    updated_at: datetime


class FieldLayout(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=100)
    hidden: list[str] = Field(default_factory=list, max_length=100)
    wide: list[str] = Field(default_factory=list, max_length=100)


class LayoutIn(BaseModel):
    # Full width, above the columns.
    top: list[str] = Field(default_factory=list, max_length=50)
    main: list[str] = Field(default_factory=list, max_length=50)
    side: list[str] = Field(default_factory=list, max_length=50)
    # Full width, below the columns.
    bottom: list[str] = Field(default_factory=list, max_length=50)
    hidden: list[str] = Field(default_factory=list, max_length=50)
    # Field group -> its fields' order, and which are hidden or full width (e.g. the PO form's).
    fields: dict[str, "FieldLayout"] = Field(default_factory=dict, max_length=20)


class LayoutOut(BaseModel):
    top: list[str]
    main: list[str]
    side: list[str]
    bottom: list[str]
    hidden: list[str]
    fields: dict[str, "FieldLayout"] = {}


class PageLayoutOut(BaseModel):
    page: str
    # What this person sees: their own layout, else the default.
    effective: LayoutOut
    default: LayoutOut
    mine: LayoutOut | None
    # Panels that can't be hidden.
    required: list[str]


# IPAM

# INET/CIDR columns come back as ipaddress objects.
IpText = Annotated[str, BeforeValidator(lambda v: v if v is None or isinstance(v, str) else str(v))]
IP_KINDS = Literal["static", "reserved", "dhcp", "do_not_use"]
CELL_STATES = Literal["free", "static", "reserved", "dhcp", "do_not_use", "gateway", "pool", "network", "broadcast"]


class VlanIn(BaseModel):
    number: int = Field(ge=1, le=4094)
    name: str = Field(default="", max_length=100)
    site: str = Field(default="", max_length=100)
    notes: str = ""


class VlanRef(ORM):
    id: int
    number: int
    name: str
    site: str


class VlanOut(VlanRef):
    notes: str
    subnet_count: int = 0


class SubnetIn(BaseModel):
    network: str = Field(min_length=1, max_length=50)  # 10.1.20.0/24
    name: str = Field(default="", max_length=200)
    vlan_id: int | None = None
    gateway: str | None = Field(default=None, max_length=50)
    dhcp_start: str | None = Field(default=None, max_length=50)
    dhcp_end: str | None = Field(default=None, max_length=50)
    dns_servers: str = Field(default="", max_length=500)
    site: str = Field(default="", max_length=100)
    notes: str = ""
    # Scheduled scans: every this many hours (0: off), by this runner (None: any that takes Python).
    scan_every_hours: int = Field(default=0, ge=0, le=24 * 31)
    scan_runner_id: int | None = None


class SubnetRef(ORM):
    id: int
    network: IpText
    name: str
    vlan: VlanRef | None = None


class SubnetUsage(BaseModel):
    # Usable host addresses (without network and broadcast).
    hosts: int
    static: int
    reserved: int
    dhcp: int
    do_not_use: int
    # Addresses in the DHCP pool.
    pool: int
    # Neither documented, the gateway, nor in the pool. None when the subnet is too big to count.
    free: int | None


class IpScanOut(ORM):
    id: int
    subnet_id: int
    run_id: int | None
    kind: Literal["network", "dhcp_dns", "snmp"]
    scheduled: bool
    created_at: datetime
    processed_at: datetime | None
    hosts_seen: int | None
    new_findings: int | None
    error: str | None
    # The run's: queued, running, succeeded...
    status: str = ""
    runner: str | None = None


class SubnetOut(ORM):
    id: int
    network: IpText
    name: str
    vlan: VlanRef | None
    gateway: IpText | None
    dhcp_start: IpText | None
    dhcp_end: IpText | None
    dns_servers: str
    site: str
    notes: str
    scan_every_hours: int
    scan_runner_id: int | None
    updated_at: datetime
    usage: SubnetUsage | None = None
    # The last network scan, and the last DHCP and DNS read (when a DHCP or DNS server is set).
    last_scan: IpScanOut | None = None
    last_dhcp_dns: IpScanOut | None = None
    dhcp_dns: bool = False
    # The last SNMP discovery (when an SNMP community is set).
    last_snmp: IpScanOut | None = None
    snmp: bool = False
    open_findings: int = 0


class IpSystemRef(ORM):
    id: int
    name: str


class IpAddressIn(BaseModel):
    address: str = Field(min_length=1, max_length=50)
    hostname: str = Field(default="", max_length=255)
    mac: str = Field(default="", max_length=30)
    kind: IP_KINDS = "static"
    device_id: int | None = None
    system_id: int | None = None
    notes: str = ""


class SwitchPortOut(BaseModel):
    """Where a MAC plugs in, from the switches' MAC tables (SNMP discovery)."""

    switch_id: int
    switch: str
    port: str
    vlan: int | None
    seen_at: datetime
    # Other MACs were on that port too (a phone with a PC behind it, a small switch, an AP).
    shared: bool


class MerakiPortOut(BaseModel):
    model_config = ConfigDict(strict=True)
    port_id: str = Field(min_length=1, max_length=100)
    enabled: bool | None = None
    status: str | None = Field(default=None, max_length=64)
    speed: str | None = Field(default=None, max_length=64)
    duplex: str | None = Field(default=None, max_length=32)
    is_uplink: bool | None = None
    client_count: int | None = Field(default=None, ge=0, le=1000000)
    errors: list[Annotated[str, Field(max_length=256)]] = Field(default_factory=list, max_length=32)
    warnings: list[Annotated[str, Field(max_length=256)]] = Field(default_factory=list, max_length=32)


class MerakiWirelessEventOut(BaseModel):
    model_config = ConfigDict(strict=True)
    occurred_at: datetime
    type: str = Field(min_length=1, max_length=64)
    subtype: str | None = Field(default=None, max_length=128)
    severity: str | None = Field(default=None, max_length=32)
    device_serial: str | None = Field(default=None, max_length=100)
    band: str | None = Field(default=None, max_length=16)
    channel: int | None = Field(default=None, ge=1, le=1000)
    rssi: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    duration_ms: int | None = Field(default=None, ge=0, le=2147483647)


class MerakiDiagnosticData(BaseModel):
    kind: Literal["wired", "wireless"]
    read_at: datetime
    window_start: datetime
    window_end: datetime
    switch_serial: str | None = None
    port: MerakiPortOut | None = None
    events: list[MerakiWirelessEventOut] = Field(default_factory=list, max_length=50)
    events_truncated: bool = False


class MerakiDiagnosticOut(BaseModel):
    network_id: str
    mac: str
    client_id: str
    client_observed_at: datetime
    attempted_at: datetime
    retry_at: datetime
    cached: bool
    error: str | None
    data: MerakiDiagnosticData | None


class MerakiClientOut(BaseModel):
    diagnostics_available: bool = False
    network_id: str
    network_name: str
    client_id: str
    mac: str
    address: str
    description: str
    status: str
    vlan: str
    switch_name: str
    switch_serial: str
    port: str
    connection: str
    ssid: str
    last_seen_at: datetime | None
    observed_at: datetime
    stale: bool


class MerakiOAuthOut(BaseModel):
    configured: bool
    connected: bool
    redirect_uri: str | None = None


class MerakiStatusOut(BaseModel):
    auth_mode: str = "api_key"
    oauth: MerakiOAuthOut | None = None
    enabled: bool
    configured: bool
    clients: int
    networks: list[str]
    last_sync_at: datetime | None
    last_sync_ok: bool | None
    last_sync_error: str | None
    last_sync_summary: str | None


class MerakiTestOut(BaseModel):
    networks: list[str]


# A client a vendor integration saw (UniFi, Juniper): cached, beside documented IP records.
class NetworkObservationOut(BaseModel):
    provider: str
    provider_label: str
    source_id: str
    source_name: str
    mac: str
    address: str
    hostname: str
    network: str
    uplink_name: str
    port: str
    ssid: str
    connection: str
    last_seen_at: datetime | None
    observed_at: datetime
    stale: bool


class IpAddressOut(ORM):
    id: int
    address: IpText
    hostname: str
    mac: str
    kind: IP_KINDS
    device: SystemDeviceRef | None
    system: IpSystemRef | None
    notes: str
    source: Literal["manual", "imported", "scan"]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    updated_at: datetime
    # The most specific subnet it's in.
    subnet: SubnetRef | None = None
    switch_port: SwitchPortOut | None = None
    meraki: MerakiClientOut | None = None
    # UniFi / Juniper observations of this MAC (vendor-neutral; Meraki has its own field).
    observations: list[NetworkObservationOut] = []


class IpCell(BaseModel):
    address: str
    state: CELL_STATES
    hostname: str = ""
    id: int | None = None


class SubnetDetailOut(SubnetOut):
    addresses: list[IpAddressOut]
    # Every address, for the at-a-glance map; None for subnets bigger than a /22.
    cells: list[IpCell] | None
    # More specific subnets inside this one.
    children: list[SubnetRef]


class IpFindingOut(ORM):
    id: int
    subnet_id: int
    address: IpText
    kind: Literal["new_host", "not_seen", "hostname_changed", "mac_changed", "in_use_do_not_use", "kind_changed"]
    scan_kind: Literal["network", "dhcp_dns", "snmp"]
    status: Literal["open", "applied", "dismissed", "cleared"]
    details: dict
    ip_address_id: int | None
    first_found_at: datetime
    last_found_at: datetime
    resolved_by: UserRef | None
    resolved_at: datetime | None


class ScanRunnerOut(ORM):
    id: int
    name: str
    last_seen_at: datetime | None


class DeviceIpOut(BaseModel):
    address: IpAddressOut
    # False: not linked to the device, but its host name matches (one click links it).
    linked: bool


# Action1 (patching, read-only)


class DevicePatchOut(ORM):
    id: int
    action1_id: str
    name: str
    device_id: int | None
    status: str
    os: str
    agent_version: str
    user: str
    last_seen_at: datetime | None
    last_boot_at: datetime | None
    missing_critical: int
    missing_other: int
    reboot_required: bool
    update_status: str
    vulnerability_status: str
    vulns_critical: int
    vulns_other: int
    organization: str
    critical_since: datetime | None
    reboot_since: datetime | None
    ticket_id: int | None
    in_action1: bool
    synced_at: datetime


class Action1StatusOut(BaseModel):
    enabled: bool
    machines: int
    matched: int
    critical: int
    reboot: int
    last_sync_at: datetime | None
    last_sync_ok: bool | None
    last_sync_summary: str | None
    last_sync_error: str | None


class Action1TestOut(BaseModel):
    ok: bool
    error: str | None = None
    organizations: list[dict] = []
    # Organizations set in Settings that these credentials can't see.
    missing_orgs: list[str] = []


# Device sweeps


class StaleDeviceOut(BaseModel):
    device: DeviceOut
    last_seen_at: datetime | None
    ad_logon_at: datetime | None
    action1_at: datetime | None
    network_at: datetime | None


class UnknownDeviceOut(BaseModel):
    kind: Literal["address", "undocumented", "action1"]
    name: str
    address: str
    mac: str
    last_seen_at: datetime | None
    ip_address_id: int | None
    finding_id: int | None
    patch_id: int | None
    notes: list[str]


class LifecycleDeviceOut(BaseModel):
    device: DeviceOut
    replace_due: date | None


class LifecycleOut(BaseModel):
    life_years: int
    warn_days: int
    warranty_soon: list[DeviceOut]
    warranty_ended: list[DeviceOut]
    # Due within a year, or overdue, soonest first.
    replace_soon: list[LifecycleDeviceOut]
    # [["2026 Q4", 3], ...]: the next 12 quarters.
    forecast: list[tuple[str, int]]
    overdue: int
    # Devices with no purchase or replace-by date.
    unknown: int


class SweepOut(BaseModel):
    stale_days: int
    recent_days: int
    stale: list[StaleDeviceOut]
    unknown: list[UnknownDeviceOut]


# Checklists (onboarding, offboarding...)


class ChecklistStep(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=4000)
    queue_id: int | None = None
    assignee_id: int | None = None
    # Due this many days from the checklist's date (negative: before it).
    offset_days: int = Field(default=0, ge=-365, le=365)
    action: Literal["disable_ad_account"] | None = None


class ChecklistTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["onboarding", "offboarding", "other"] = "other"
    description: str = ""
    items: list[ChecklistStep] = Field(default_factory=list, max_length=100)
    is_active: bool = True


CHECKLIST_KINDS = Literal["onboarding", "offboarding", "other"]


class ChecklistTemplateOut(ORM):
    id: int
    name: str
    kind: CHECKLIST_KINDS
    description: str
    items: list[ChecklistStep]
    is_active: bool
    updated_at: datetime


class StartChecklistIn(BaseModel):
    template_id: int
    # The start date (onboarding) or last day (offboarding); steps are due from it.
    key_date: date | None = None
    # Who it's for: someone with an account (needed to disable an AD account), or just a name (a new starter).
    for_user_id: int | None = None
    for_name: str = Field(default="", max_length=200)


class ChecklistDateIn(BaseModel):
    key_date: date


class ChecklistItemOut(ORM):
    id: int
    title: str
    offset_days: int
    due_on: date | None
    action: str | None
    run_id: int | None
    child_ticket: ChildTicketOut | None


class TicketChecklistOut(ORM):
    id: int
    name: str
    kind: CHECKLIST_KINDS
    for_name: str
    for_user_id: int | None
    key_date: date | None
    created_at: datetime
    items: list[ChecklistItemOut]


# Changes


class ChangeIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["standard", "normal", "emergency"] = "normal"
    # A standard change comes from a pre-approved template.
    template_id: int | None = None
    description: str = ""
    implementation_plan: str = ""
    rollback_plan: str = ""
    test_plan: str = ""
    impact: int = Field(default=1, ge=1, le=3)
    likelihood: int = Field(default=1, ge=1, le=3)
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    owner_id: int | None = None
    ticket_ids: list[int] = Field(default_factory=list, max_length=100)
    device_ids: list[int] = Field(default_factory=list, max_length=500)
    system_ids: list[int] = Field(default_factory=list, max_length=100)


class ChangeActionIn(BaseModel):
    action: Literal["submit", "approve", "reject", "start", "complete", "fail", "roll_back", "cancel", "reopen"]
    note: str = Field(default="", max_length=4000)


class ChangeTicketRef(ORM, Labelled):
    id: int
    subject: str
    status: TicketStatus
    created_at: datetime


class ChangeEventOut(ORM):
    id: int
    at: datetime
    actor: UserRef | None
    type: str
    note: str


class ChangeSummaryOut(ORM):
    id: int
    label: str = ""
    title: str
    kind: Literal["standard", "normal", "emergency"]
    status: Literal["draft", "awaiting_approval", "approved", "rejected", "in_progress", "completed", "failed", "rolled_back", "cancelled"]
    risk: int = 0
    risk_level: Literal["low", "medium", "high"] = "low"
    planned_start: datetime | None
    planned_end: datetime | None
    owner: UserRef | None
    approved_at: datetime | None
    updated_at: datetime
    # "action1:..." when an Action1 patch automation made it.
    source: str = ""


class ChangeOut(ChangeSummaryOut):
    template_id: int | None
    description: str
    implementation_plan: str
    rollback_plan: str
    test_plan: str
    impact: int
    likelihood: int
    requested_by: UserRef | None
    approved_by: UserRef | None
    outcome: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    tickets: list[ChangeTicketRef]
    devices: list[SystemDeviceRef]
    systems: list[IpSystemRef]
    events: list[ChangeEventOut]
    needs_approval: bool = False
    can_approve: bool = False
    can_edit: bool = False
    # Other open changes in an overlapping window, and the devices/systems they share with this one.
    conflicts: list["ChangeConflictOut"] = []


class ChangeConflictOut(BaseModel):
    change: ChangeSummaryOut
    shared: list[str]


class ChangeTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    pre_approved: bool = False
    description: str = ""
    implementation_plan: str = ""
    rollback_plan: str = ""
    test_plan: str = ""
    impact: int = Field(default=1, ge=1, le=3)
    likelihood: int = Field(default=1, ge=1, le=3)
    is_active: bool = True


class ChangeTemplateOut(ORM):
    id: int
    name: str
    pre_approved: bool
    description: str
    implementation_plan: str
    rollback_plan: str
    test_plan: str
    impact: int
    likelihood: int
    is_active: bool
    updated_at: datetime


# Knowledge base


class KbArticleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=100_000)
    keywords: str = Field(default="", max_length=500)
    category: str | None = Field(default=None, max_length=100)
    audience: Literal["everyone", "staff"] = "staff"
    status: Literal["draft", "published", "archived"] = "draft"


class KbArticleSummaryOut(ORM):
    id: int
    title: str
    excerpt: str = ""
    category: str | None
    audience: Literal["everyone", "staff"]
    status: Literal["draft", "published", "archived"]
    views: int
    helpful_yes: int
    helpful_no: int
    published_at: datetime | None
    updated_at: datetime


class KbArticleOut(KbArticleSummaryOut):
    body: str
    keywords: str
    source_ticket_id: int | None
    author: UserRef | None
    updated_by: UserRef | None
    created_at: datetime
    can_edit: bool = False


class KbVersionOut(BaseModel):
    number: int
    title: str
    note: str
    author: UserRef | None
    created_at: datetime
    is_current: bool = False


class KbVersionDetailOut(KbVersionOut):
    body: str
    keywords: str
    # Against the version before it (unified diff of the text, title first).
    diff: str


class KbFileOut(BaseModel):
    id: str
    filename: str
    url: str
    # Ready to paste into the article.
    markdown: str


class KbFeedbackIn(BaseModel):
    helpful: bool


class KbLinkIn(BaseModel):
    article_id: int


class SimilarTicketOut(BaseModel):
    id: int
    label: str
    subject: str
    status: str
    category: str | None
    resolved_at: datetime | None
    score: float


class TicketKbOut(BaseModel):
    """For a ticket: articles that match it, solved tickets like it, and articles already used on it."""

    articles: list[KbArticleSummaryOut]
    similar: list[SimilarTicketOut]
    linked: list[KbArticleSummaryOut]


# Satisfaction surveys


class SurveyOut(BaseModel):
    """What the survey page shows (by its link, or on the ticket in the portal)."""

    ticket_label: str
    subject: str
    question: str
    ratings: dict[int, str]
    rating: int | None
    comment: str
    expired: bool


class SurveyAnswerIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=4000)


class SurveyResultOut(BaseModel):
    ticket_id: int
    ticket_label: str
    subject: str
    requester: str | None
    assignee: str | None
    queue: str | None
    rating: int | None
    comment: str
    sent_at: datetime
    answered_at: datetime | None


class SurveySummaryOut(BaseModel):
    days: int
    sent: int
    answered: int
    average: float | None
    # rating -> how many
    by_rating: dict[int, int]
    recent: list[SurveyResultOut]


# Permissions


class PermissionRuleIn(BaseModel):
    who: Literal["agents", "admins", "groups"]
    group_ids: list[int] = []


class PermissionOut(BaseModel):
    key: str
    area: str
    label: str
    help: str
    default: str
    who: Literal["agents", "admins", "groups"]
    group_ids: list[int]


class NetworkNeighborOut(ORM):
    local_port: str
    remote_name: str
    remote_port: str
    remote_port_desc: str
    remote_chassis: str
    remote_address: IpText | None
    # The neighbour, when it's discovered gear too.
    remote_id: int | None = None


class NetworkDeviceOut(ORM):
    id: int
    address: IpText
    sys_name: str
    sys_descr: str
    sys_object_id: str
    location: str
    contact: str
    vendor: str
    model: str
    serial: str
    kind: Literal["switch", "printer", "other"]
    uptime_seconds: int | None
    device: SystemDeviceRef | None
    subnet_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    neighbor_count: int = 0
    mac_count: int = 0


class SwitchPortMacOut(BaseModel):
    mac: str
    vlan: int | None
    # What's documented for it in IP addresses, if anything.
    address: IpText | None = None
    hostname: str = ""
    device: SystemDeviceRef | None = None


class SwitchPortGroupOut(BaseModel):
    port: str
    # What LLDP sees on it (a phone, an access point, another switch).
    neighbor: str = ""
    # Leads to other discovered gear, or carries many MACs.
    uplink: bool = False
    macs: list[SwitchPortMacOut]


class NetworkDeviceDetailOut(NetworkDeviceOut):
    neighbors: list[NetworkNeighborOut]
    ports: list[SwitchPortGroupOut]


class NetworkDeviceLinkIn(BaseModel):
    device_id: int | None


class WarrantyEntitlementOut(BaseModel):
    name: str = ""
    type: str = ""
    start: date | None = None
    end: date | None = None


class DeviceWarrantyOut(BaseModel):
    """What the maker says (Dell TechDirect, Lenovo), as of the last lookup."""

    model_config = ConfigDict(from_attributes=True)

    vendor: Literal["dell", "lenovo"] | None
    # The maker's API can be asked (credentials set, and a Dell or Lenovo device with a serial).
    available: bool
    # Why not, when not available.
    reason: str = ""
    serial: str = ""
    checked_at: datetime | None = None
    error: str | None = None
    product: str = ""
    shipped_on: date | None = None
    purchased_on: date | None = None
    warranty_ends: date | None = None
    entitlements: list[WarrantyEntitlementOut] = []
    # The device's own warranty end or purchase date differs from the maker's.
    differs: bool = False
