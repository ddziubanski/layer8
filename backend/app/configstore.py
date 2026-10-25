# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings admins change in the app (Settings screen) and the installer sets
(python -m app.cli settings import answers.json).

Each section is one app_settings row; secrets are files in HELPDESK_SECRETS_DIR (mode
0600), written but never read back through the API. Stored values are laid over
app.config.settings in every process, so code keeps reading settings.portal_url and so on.
A HELPDESK_* environment variable still wins: the field shows as set on the server.

Only bootstrap values stay environment-only: database URLs, paths and dev switches.
"""

import base64
import binascii
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_origin

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models import AppSetting, User
from app.workspace import MODULES as WORKSPACE_MODULES

log = logging.getLogger(__name__)
# Fields set by the environment (or .env) when the process started: those win over stored values.
FROM_ENVIRONMENT = frozenset(settings.model_fields_set)
# How stale another process's change may be before a request picks it up.
RELOAD_SECONDS = 5.0


class SettingsError(ValueError):
    """A value can't be stored; the message is safe to show."""


def _http_url(v: str) -> None:
    if v and not re.match(r"^https?://[^\s/]+", v):
        raise SettingsError("Must start with https:// (or http://), e.g. https://helpdesk.corp.example")


def _ldap_urls(v: str) -> None:
    for url in (u.strip() for u in v.split(",")):
        if url and not re.match(r"^ldaps?://[^\s/]+", url):
            raise SettingsError(f"{url!r} should look like ldaps://dc1.corp.example")


def _certificates(v: str) -> None:
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", v, re.S)
    if v and not blocks:
        raise SettingsError("Paste the CA certificate in PEM form (-----BEGIN CERTIFICATE----- ...)")
    for block in blocks:
        body = "".join(block.splitlines()[1:-1])
        try:
            der = base64.b64decode(body, validate=True)
        except binascii.Error:
            der = b""
        # A certificate is a DER SEQUENCE (0x30), and never tiny.
        if len(der) < 100 or der[0] != 0x30:
            raise SettingsError("One of the certificates is damaged")


def _guid(v: str) -> None:
    if v and not re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", v):
        raise SettingsError("Should be a GUID like 00000000-0000-0000-0000-000000000000 (from the Entra admin center)")


def _email(v: str) -> None:
    if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        raise SettingsError("Should be an email address, e.g. helpdesk@example.com")


def _domains(v: list[str]) -> None:
    for d in v:
        if not re.fullmatch(r"@?[a-z0-9-]+(\.[a-z0-9-]+)+", d.lower()):
            raise SettingsError(f"{d!r} isn't a domain name, e.g. example.com")


def _org_ids(v: list[str]) -> None:
    for o in v:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", o.strip()):
            raise SettingsError(f"{o!r} isn't an Action1 organization ID (Test connection lists them)")


def _meraki_networks(v: list[str]) -> None:
    if len(v) > 20 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n) for n in v):
        raise SettingsError("Choose at most 20 network IDs, one per line (e.g. N_123456)")


def _unifi_ids(v: list[str]) -> None:
    if len(v) > 50 or any(not re.fullmatch(r"[A-Za-z0-9:._-]{1,200}", n) for n in v):
        raise SettingsError("At most 50 IDs, one per line, as UniFi shows them (letters, digits, : . _ -)")


def _https_url(v: str) -> None:
    if v and not re.match(r"^https://[^\s/]+/?$", v):
        raise SettingsError("The console's address, e.g. https://unifi.corp.example (https, no path)")


def _prism_url(v: str) -> None:
    if v and not re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?", v):
        raise SettingsError("Prism Central's address, e.g. https://prism.corp.example:9440 (https, no path)")


def _vm_names(v: list[str]) -> None:
    if len(v) > 200 or any(not 0 < len(n) <= 255 or any(ord(c) < 32 for c in n) for n in v):
        raise SettingsError("At most 200 VM names, one per line, as Prism Central shows them")


def _junos_switches(v: str) -> None:
    from app.junos import JunosError, parse_switches

    try:
        parse_switches(v)
    except JunosError as exc:
        raise SettingsError(str(exc)) from None


def _domain(v: str) -> None:
    if v:
        _domains([v])


def _group_lists(v: list[str]) -> None:
    for line in v:
        label, _, prefix = line.partition(":")
        if not label.strip() or not prefix.strip():
            raise SettingsError(f"{line!r}: write a label, a colon and the start of the names (Distribution lists: [EMAIL]) or an OU (Distribution lists: OU=Distribution Lists,DC=corp,DC=example)")
        v = prefix.strip().upper()
        if v.startswith(("OU=", "CN=")) and ",DC=" not in v:
            raise SettingsError(f"{line!r}: give the OU's full DN, ending in its DC= parts")


def _name_format(v: str) -> None:
    from app.adchanges import ADChangeError, account_name

    try:
        account_name(v, "John", "Smith")
    except ADChangeError as exc:
        raise SettingsError(str(exc)) from None


def _length(lo: int, hi: int) -> Callable[[str], None]:
    def check(v: str) -> None:
        if not lo <= len(v) <= hi:
            raise SettingsError(f"Must be {lo}–{hi} characters" if lo else f"At most {hi} characters")

    return check


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours (1 to 21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _colour_under_white_text(v: str) -> None:
    """White text goes on it (buttons, sidebar): it must be dark enough to read (WCAG AA, 4.5:1)."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        raise SettingsError("Should be a colour like #0f6b5c")
    if (ratio := contrast(v, "#ffffff")) < 4.5:
        raise SettingsError(f"Too light: white text on it has a contrast of {ratio:.1f}:1, and needs at least 4.5:1. Pick a darker shade")


def _hhmm(v: str) -> None:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise SettingsError("Should be a time like 02:00 (24-hour)")


def _absolute_path(v: str) -> None:
    if v and not v.startswith("/"):
        raise SettingsError("Should be a full path on the server, e.g. /mnt/backups/helpdesk")


def _passphrase(v: str) -> None:
    if len(v) < 12:
        raise SettingsError("Use at least 12 characters")


def _label_format(v: str) -> None:
    from app.numbering import NumberingError, check_format

    try:
        check_format(v)
    except NumberingError as exc:
        raise SettingsError(str(exc)) from None


def _between(lo: int, hi: int) -> Callable[[int], None]:
    def check(v: int) -> None:
        if not lo <= v <= hi:
            raise SettingsError(f"Must be between {lo} and {hi}")

    return check


@dataclass(frozen=True)
class Field:
    name: str  # attribute of app.config.Settings, and HELPDESK_<NAME> overrides it
    label: str
    help: str = ""
    secret: bool = False
    check: Callable[[Any], None] | None = None
    # Pick one of (value, label).
    choices: tuple[tuple[str, str], ...] = ()
    # Kept as this file in the secrets folder; the setting holds its path (e.g. a CA certificate).
    file: str | None = None
    # Shown as a colour picker / a multi-line box.
    colour: bool = False
    multiline: bool = False

    @property
    def kind(self) -> str:
        annotation = Settings.model_fields[self.name].annotation
        if self.secret:
            return "secret"
        if self.file:
            return "pem"
        if self.choices:
            return "choice"
        if self.colour:
            return "colour"
        if self.multiline:
            return "multiline"
        if annotation is bool:
            return "bool"
        if annotation is int:
            return "int"
        if get_origin(annotation) is list:
            return "list"
        return "text"

    def parse(self, value: Any) -> Any:
        try:
            parsed = TypeAdapter(Settings.model_fields[self.name].annotation).validate_python(value)
        except ValidationError:
            raise SettingsError(f"{self.label}: expected {self.kind}") from None
        if isinstance(parsed, str):
            parsed = parsed.strip()
        if isinstance(parsed, list):
            parsed = [str(v).strip() for v in parsed if str(v).strip()]
        if self.choices and parsed not in {c for c, _ in self.choices}:
            raise SettingsError(f"{self.label}: pick one of {', '.join(c for c, _ in self.choices)}")
        if self.check:
            try:
                self.check(parsed)
            except SettingsError as exc:
                raise SettingsError(f"{self.label}: {exc}") from None
        return parsed


def _teams_url(v: str) -> None:
    from app.teams import TeamsError, validate_url

    try:
        validate_url(v)
    except TeamsError as exc:
        raise SettingsError(str(exc)) from None


def _hours(v: str) -> None:
    from app.hours import HoursError, check_week

    try:
        check_week(v)
    except HoursError as exc:
        raise SettingsError(str(exc)) from None


def _closed_days(v: str) -> None:
    from app.hours import HoursError, check_closed

    try:
        check_closed(v)
    except HoursError as exc:
        raise SettingsError(str(exc)) from None


SECTIONS: dict[str, list[Field]] = {
    "modules": [Field(name, label, help) for _, name, label, help in WORKSPACE_MODULES],
    "hours": [
        Field("desk_hours", "Desk hours", "When technicians are in, one line per set of days, e.g. Mon-Fri 08:00-17:00 or Tue 08:00-12:00, 13:00-17:00 (24-hour clock, the server's time zone). Days not listed are closed, and a later line replaces earlier hours for its days. A queue can have its own hours in Settings > Queues.", multiline=True, check=_hours),
        Field("desk_closed_days", "Closed days", "One per line: a date and an optional name, e.g. 2026-12-25 Christmas Day. Every queue is closed on these days.", multiline=True, check=_closed_days),
        Field("sla_clock", "SLA clock", "Desk hours: SLA times count only while the ticket's queue is open (its own hours, or the desk hours above), skipping closed days, so a 4-hour SLA logged on Friday at 16:00 is due Monday at 11:00. Around the clock: every minute counts. Applies to new tickets and priority changes.", choices=(("desk", "Desk hours"), ("always", "Around the clock"))),
        Field("sla_pause_waiting", "Stop the SLA clock while waiting on the user", "While a ticket is Waiting on user, its SLA doesn't run and it never shows as at risk or breached; the time left comes back when it's worked again."),
        Field("unaccepted_alert_enabled", "Alert when nobody takes a ticket", "A new or open ticket with nobody assigned for the time below alerts everyone who could take it (the queue's owners, or every technician who can see the queue), once: by email (reply #assign me to take it) or in a Teams channel. The console's top bar shows how many are waiting."),
        Field("unaccepted_alert_minutes", "Not taken for (minutes)", "Counted like the SLA: in desk hours, unless the SLA clock runs around the clock.", check=_between(5, 1440)),
        Field("unaccepted_alert_via", "Alert by", "Email: each person who could take it. Teams: one post in the channel of the Settings > Teams workflow, listing the tickets with links; tickets in restricted queues are never posted there (they're emailed). Both: the two together.", choices=(("email", "Email"), ("teams", "Teams channel"), ("both", "Email and Teams channel"))),
        Field("after_hours_message", "After-hours message", "Shown on the portal for a queue that is closed. {next} becomes when someone is next expected (e.g. \"on Monday at 08:00\"), {queue} the queue's name.", multiline=True, check=_length(0, 1000)),
        Field("presence_on_portal", "Show technicians' Teams status on the portal", "Each queue's owners with their Teams status (available, busy, away, offline), status only: no chat or call links. Needs the Email tab's app registration with the application permissions Presence.Read.All and User.ReadBasic.All (admin consent); checked every minute."),
    ],
    "requester_emails": [
        Field("email_ack_template", "We received your request", "Sent when a ticket is opened by email or the portal. {first_name}, {name}, {ticket}, {subject}, {technician}, {queue} and {portal} are filled in. The ticket tag goes in the subject, and Settings > Branding's email footer is added.", multiline=True, check=_length(1, 5000)),
        Field("email_resolved_enabled", "Email the requester when a ticket is resolved", "Off: they hear about it only from a technician's reply. A reply to the email reopens the ticket."),
        Field("email_resolved_template", "Resolved", "", multiline=True, check=_length(1, 5000)),
        Field("email_assigned_enabled", "Email the requester when someone takes their ticket", "Each time a technician is assigned, including when it moves to another technician. Not when it's unassigned."),
        Field("email_assigned_template", "Assigned", "", multiline=True, check=_length(1, 5000)),
    ],
    "signin": [
        Field("totp_required_admins", "Require two-factor sign-in for admins with a local password", "They set up an authenticator app (My preferences > Two-factor sign-in) the next time they sign in, before they can do anything else. Anyone can turn two-factor on for themselves either way."),
        Field("entra_sign_in_enabled", "Sign in with Microsoft (Entra ID)", "Adds a Sign in with Microsoft button. Only people who already have an account here can use it, matched by their Entra ID object or by UPN (not the email claim); accounts with a local password (the break-glass admin) can't. Entra's own MFA and Conditional Access apply. See docs/entra-sign-in.md."),
        Field("entra_tenant_id", "Tenant ID", "Your Microsoft Entra tenant (directory) ID.", check=_guid),
        Field("entra_client_id", "Application (client) ID", "A separate app registration for sign-in, with the redirect URI https://<portal address>/api/auth/entra/callback (Web).", check=_guid),
        Field("entra_client_secret", "Client secret", "Written to a secret file and never shown back.", secret=True, check=_length(0, 512)),
    ],
    "teams": [
        Field("teams_chat_enabled", "Allow technician Teams connections", "Optional delegated Microsoft sign-in. Technicians connect their own account under My preferences."),
        Field("teams_tenant_id", "Teams directory (tenant) ID", "Single-tenant Entra application; separate from the mailbox integration.", check=_guid),
        Field("teams_client_id", "Teams application (client) ID", "Configure a Web redirect URI: your HTTPS portal address followed by /teams/callback.", check=_guid),
        Field("teams_client_secret", "Teams application secret", "The secret value, stored server-side. See docs/teams.md for delegated permissions.", secret=True),
        Field("copilot_enabled", "Copilot on tickets (preview)", "Technicians ask Microsoft 365 Copilot from a ticket, as themselves, with reviewed ticket text as context if they choose (internal notes require a separate choice). Needs technician Teams connections (above), a Microsoft 365 Copilot licence per technician, and these delegated permissions on the Teams app registration with admin consent: Sites.Read.All, Mail.Read, People.Read.All, OnlineMeetingTranscript.Read.All, Chat.Read, ChannelMessage.Read.All, ExternalItem.Read.All. Technicians reconnect once in My preferences after it's turned on. Microsoft's Copilot Chat API is in beta (not supported for production use)."),
        Field("teams_enabled", "Send Teams queue summaries", "Off by default. Shares aggregate counts from non-restricted queues only; no ticket subjects, people or notes."),
        Field("teams_webhook_url", "Workflows webhook URL", "A signed Teams Workflows URL. Kept as a secret and never shown back. See docs/teams.md for setup.", secret=True, check=_teams_url),
        Field("teams_summary_minutes", "Summary interval (minutes)", "Sends a periodic snapshot, including zero counts. Settings > Status can pause or run it now.", check=_between(15, 1440)),
    ],
    "updates": [
        Field("updates_enabled", "Enable GitHub release updates", "Allows explicit administrator release checks and reviewed signed updates from ddziubanski/layer8. No automatic installs; leave off for offline appliances."),
    ],
    "general": [
        Field("portal_url", "Portal address", "The address people open, e.g. https://helpdesk.corp.example. Used in ticket links in emails.", check=_http_url),
        Field("source_repository", "Source code address", "Where people can get Layer 8's source, linked in the console, portal and sign-in page beside the source download (the AGPL asks you to offer the source of what you run). A modified copy should point at its own repository. Empty: the download link only.", check=_http_url),
        Field("session_hours", "Sign-in lasts (hours)", "People are signed out after this long without using the app.", check=_between(1, 720)),
        Field("max_upload_mb", "Largest attachment (MB)", "Per file, from the portal and the agent console.", check=_between(1, 1024)),
        Field("ticket_first_number", "First ticket number", "The number the next ticket gets, e.g. 10000. Can only move forward once there are tickets.", check=_between(1, 10**12)),
        Field("po_first_number", "First purchase order number", "The number the next purchase order gets. Can only move forward once there are POs.", check=_between(1, 10**12)),
        Field("po_label_format", "Purchase order label", "How PO numbers are shown, like the ticket label: e.g. PO-{n:05}, {yyyy}-PO{n}.", check=_label_format),
        Field("ticket_label_format", "Ticket label", "How ticket numbers are shown and put in email subjects: {n} is the number, {n:06} pads it to six digits, {yyyy}/{yy} the year it was opened. E.g. #{n}, HD-{n}, INC{n:06}, {yyyy}-{n}.", check=_label_format),
        Field("action1_enabled", "Use Action1 (patching)", "Shows Settings > Action1, patch status on devices and tickets, and Action1 patch nights on the change calendar, and reads Action1 on a schedule. Off: all of that is hidden and nothing is read."),
        Field("sql_enabled", "Allow SQL (rules, reports, queries)", "SQL rules, SQL reports and SQL library queries read the database directly. They can see every ticket, including those in restricted queues (such as HR) and internal notes, whoever runs them. Anyone allowed to write or run them can read that. Turn on only if that's acceptable."),
    ],
    "directory": [
        Field("ldap_url", "Domain controllers", "One or more, comma-separated, tried in order: ldaps://dc1.corp.example,ldaps://dc2.corp.example. Empty: no directory, local accounts only.", check=_ldap_urls),
        Field("ldap_kind", "Directory type", choices=(("ad", "Active Directory"), ("generic", "Other LDAP"))),
        Field("ldap_bind_dn", "Service account", "Read-only account used to search and sync, e.g. CN=svc-helpdesk,OU=Service Accounts,DC=corp,DC=example."),
        Field("ldap_bind_password", "Service account password", secret=True),
        Field("ldap_base_dn", "Search base", "Where people are found, e.g. DC=corp,DC=example or an OU."),
        Field("ldap_user_filter", "Which accounts", "LDAP filter for the people to sync. Narrow it so service accounts aren't synced as requesters."),
        Field("ldap_group_base_dn", "Group search base", "Where to look for groups. Empty: the search base."),
        Field("ldap_computer_base_dn", "Computer search base", "Where to look for computers, which become devices (AD only), e.g. OU=Workstations,DC=corp,DC=example. Empty: the search base."),
        Field("ad_change_group_bases", "Groups technicians may change", "AD changes from tickets: one OU per line (DN); only groups (security groups and distribution lists) under these can be added to or removed from a requester, e.g. OU=Access Groups,DC=corp,DC=example. Empty: no group changes. The change runs on a PowerShell relay whose account has those rights in AD."),
        Field("ad_change_ou_bases", "OUs accounts may be moved into", "One OU per line (DN); a requester's account can be moved into these or any OU below them. Empty: no OU moves."),
        Field("ad_runner_name", "Relay for AD account changes", "The relay's name for AD changes, account creation, deferred enabling and offboarding. Select a dedicated integration relay for privileged AD credentials. Empty: any general relay that accepts PowerShell; dedicated hosts require an explicit selection.", check=_length(0, 100)),
        Field("ad_group_lists", "Group lists to tick from", "One per line: a label, a colon, then either an OU (Distribution lists: OU=Distribution Lists,DC=corp,DC=example: every group in it, and technicians may change those groups) or the start of the groups' names (Distribution lists: [EMAIL]: groups under the OUs above whose name starts with it). Each list is offered as tick boxes when adding someone to groups (on a ticket, a new account, a template).", check=_group_lists),
        Field("ad_new_user_ou", "New accounts go in", "Onboarding: the OU new AD accounts are created in, unless the onboarding template names one, e.g. OU=Staff,DC=corp,DC=example. Must be within the OUs above. Empty: technicians can't create accounts."),
        Field("ad_new_user_domain", "New accounts' email and UPN domain", "e.g. example.com: the account jsmith gets jsmith@example.com as its UPN, and that is suggested as the email address (the technician can change it).", check=_domain),
        Field("ad_new_user_name_format", "New account name", "Built from the person's name, lower case, at most 20 characters: {first}, {last}, {f} (first initial), {l} (last initial). E.g. {f}{last} gives jsmith, {first}.{last} gives john.smith. A number is added if it's taken.", check=_name_format),
        Field("ad_new_user_password_length", "First password length", "Generated for each new account and shown once to the technician who creates it; never stored.", check=_between(12, 64)),
        Field("ad_new_user_change_password", "New accounts must change their password at first sign-in"),
        Field("ad_new_user_enabled", "New accounts are enabled straight away", "Off: they're created disabled, and someone enables them on the start date."),
        Field("ad_new_user_proxy_address", "Set the primary SMTP address (proxyAddresses) on new accounts", "For hybrid sites without an Exchange server: Entra Connect syncs it to the mailbox. Leave off if Exchange manages addresses."),
        Field("ldap_computer_filter", "Which computers", "LDAP filter for the computers to sync as devices. Empty: don't sync computers."),
        Field("ldap_ca_file", "CA certificate", "The CA that issued the domain controllers' LDAPS certificates, in PEM form. Empty: the server's trusted CAs.", check=_certificates, file="ldap-ca.pem"),
        Field("ldap_start_tls", "Use StartTLS on ldap://"),
        Field("ldap_allow_insecure", "Allow plain ldap:// without TLS", "Sends passwords in the clear. Lab use only."),
        Field("ldap_timeout_seconds", "Timeout (seconds)", check=_between(1, 120)),
        Field("ldap_sync_minutes", "Sync every (minutes)", check=_between(5, 1440)),
    ],
    "email": [
        Field("mailbox", "Service desk mailbox", "The shared mailbox tickets are emailed to and sent from, e.g. helpdesk@example.com. Empty: email intake and sending are off.", check=_email),
        Field("graph_tenant_id", "Directory (tenant) ID", "From the app registration in the Entra admin center.", check=_guid),
        Field("graph_client_id", "Application (client) ID", "From the same app registration.", check=_guid),
        Field("graph_client_secret", "Client secret", "The secret's value (not its ID). Update it here before it expires.", secret=True),
        Field("mail_allowed_domains", "Accept email from", "One domain per line, e.g. example.com. Senders from these domains become requesters automatically; others are rejected unless they're already users.", check=_domains),
        Field("mail_poll_seconds", "Check the mailbox every (seconds)", check=_between(10, 3600)),
        Field("email_commands", "Technicians can work tickets by email", "Lines like #close, #assign me, #priority high or #internal in a technician's email act on the ticket (Tickets Anywhere). Only staff who can see the ticket's queue."),
        Field("email_commands_internal_only", "Only from mail Exchange confirms is internal", "Email commands run only when Exchange Online marks the message as sent by an authenticated sender in your organization (X-MS-Exchange-Organization-AuthAs: Internal), so someone forging a technician's address can't close or reassign tickets. Turn off only if your mail doesn't arrive through Exchange Online."),
    ],
    "branding": [
        Field("product_name", "Product name", "Shown in the header, on the sign-in page and in the browser tab.", check=_length(1, 60)),
        Field("sign_in_message", "Sign-in message", "A line under the sign-in form, e.g. who to call if you can't get in.", check=_length(0, 300)),
        Field("background_in_console", "Show the background behind the agent console too", "It's always shown on the sign-in page and in the portal."),
        Field("portal_welcome", "Portal welcome text", "Shown at the top of the portal's home page, e.g. opening hours or what to try first. Plain text.", check=_length(0, 1000), multiline=True),
        Field("email_footer", "Email footer", "Added to the end of every email to requesters and every emailed report, e.g. the desk's phone number. Plain text.", check=_length(0, 1000), multiline=True),
    ],
    "signin_appearance": [
        Field("sign_in_heading", "Welcome heading", "Optional plain-text heading above the sign-in form. Authentication and role routing stay unchanged.", check=_length(0, 100)),
        Field("sign_in_layout", "Card placement", choices=(("center", "Center"), ("left", "Left"), ("right", "Right"))),
        Field("sign_in_card_size", "Card width", choices=(("standard", "Standard"), ("wide", "Wide"))),
        Field("sign_in_backdrop", "Backdrop style", "Used behind the uploaded image, or by itself when no image is uploaded.", choices=(("plain", "Theme default"), ("ocean", "Ocean gradient"), ("grid", "Network grid"))),
        Field("sign_in_background_fit", "Background image fit", choices=(("cover", "Fill screen (crop edges)"), ("contain", "Show whole image"))),
        Field("sign_in_background_position", "Background image focus", choices=(("center", "Center"), ("top", "Top"), ("bottom", "Bottom"), ("left", "Left"), ("right", "Right"))),
        Field("sign_in_background_overlay", "Image darkening (%)", "0–85. Applies only to the sign-in image, keeping the card readable. The portal keeps its own existing background.", check=_between(0, 85)),
    ],
    "backup": [
        Field("backup_dir", "Backup folder", "A folder on the server, normally a network share the installer mounted (SMB or NFS), e.g. /mnt/backups/helpdesk. Empty: no scheduled backups.", check=_absolute_path),
        Field("backup_time", "Back up daily at", "Server time, 24-hour, e.g. 02:00.", check=_hhmm),
        Field("backup_keep", "Backups to keep", "Older ones are deleted after each backup.", check=_between(1, 365)),
        Field(
            "backup_passphrase",
            "Backup passphrase",
            "Encrypts every backup. Keep a copy somewhere safe away from this server: without it a backup can't be restored. Changing it only affects new backups.",
            secret=True,
            check=_passphrase,
        ),
    ],
    "purchasing": [
        Field("public_receiving", "Receiving page without signing in", "The receiving clerk opens /receive (or scans the QR code on a PO's receiving sheet), enters the PO number and its receiving code, and marks what arrived. Off: receiving only in the console."),
        Field("receiving_email", "Receiving's email address", "The receiving dock or clerk. They get a heads-up when a PO is ordered: what's coming, when, and the receiving page link and code.", check=_email),
        Field("receiving_heads_up", "Send receiving a heads-up when a PO is ordered", "Also possible from each PO with \"Send heads-up\"."),
        Field("receiving_notify", "Also tell this address when something arrives", "E.g. the service desk mailbox. The PO's requester and approver are always told.", check=_email),
    ],
    "theme": [
        Field("theme_preset", "Appearance", "Midnight, Ember and Ultraviolet are fixed dark palettes: navy and cyan; plum, violet and amber; near-black, violet and gold. Choose Custom colours to restore your saved colours and dark-mode preference.", choices=(("custom", "Custom colours"), ("midnight", "Midnight command center"), ("ember", "Ember"), ("ultraviolet", "Ultraviolet"))),
        Field("theme_brand", "Main colour", "For Custom colours: buttons, links, selected items and report bars. White text goes on it, so it must be dark enough.", check=_colour_under_white_text, colour=True),
        Field("theme_sidebar", "Sidebar colour", "For Custom colours: the console's sidebar and the portal's top bar, with white text.", check=_colour_under_white_text, colour=True),
        Field("dark_mode", "Dark mode", "For Custom colours. Midnight is always dark.", choices=(("off", "Off"), ("auto", "Follow each person's device setting"), ("on", "Always dark"))),
    ],
    "ticket_fields": [
        Field("require_resolution_type", "Resolution type is required when resolving or closing"),
        Field("require_closure_reason", "Closure reason is required when resolving or closing"),
        Field("block_parent_close", "A ticket with open child tickets can't be resolved or closed"),
        Field("departments_from_ad", "Add new AD departments automatically", "When a ticket's requester has a department in AD that isn't on the list yet, it's added."),
    ],
    "changes": [
        Field("change_label_format", "Change label", "How changes are labelled: {n} is the number, e.g. CHG-{n:05} gives CHG-00012.", check=_label_format),
        Field(
            "change_approval_level",
            "Normal changes need approval from risk",
            "Risk is impact x likelihood (each low, medium, high). Standard changes from a pre-approved template never need it; emergency changes are approved afterwards.",
            choices=(("low", "Low and up (every change)"), ("medium", "Medium and up"), ("high", "High only")),
        ),
        Field("change_approvers_group", "Who approves changes", "A group's name as on Settings > Groups (local, or synced from AD), e.g. Change Board. Empty: admins. Nobody approves their own change.", check=_length(0, 200)),
    ],
    "sweeps": [
        Field("sweep_stale_days", "A device is stale after (days)", "With no sign of life from any source: AD's last logon (up to ~14 days behind), Action1's last seen, or a network scan seeing its address.", check=_between(7, 3650)),
        Field("sweep_recent_days", "Unknown things count if seen in the last (days)", "Addresses scans saw that aren't tied to a device or system, and Action1 machines without a device here.", check=_between(1, 365)),
        Field("device_life_years", "Replace devices after (years)", "From the purchase date, unless a device has its own replace-by date. Used by Devices > Lifecycle and its forecast.", check=_between(1, 15)),
        Field("warranty_warn_days", "Flag warranties ending within (days)", check=_between(7, 365)),
        Field("sweep_weekly_ticket", "Open a ticket with the sweep each week", "One ticket listing stale and unknown devices, when there are any, through the routing rules (subject \"Device sweep\")."),
    ],
    "surveys": [
        Field(
            "survey_percent", "Send a survey for this % of solved tickets",
            "Picked at random when a ticket is first resolved or closed: 10 asks about one ticket in ten, 100 every ticket. 0: no surveys. Child tasks and merged tickets are never surveyed.",
            check=_between(0, 100),
        ),
        Field("survey_cooldown_days", "Ask the same person at most once every (days)", "So people who raise a lot of tickets aren't asked every time. 0: no limit.", check=_between(0, 365)),
        Field("survey_expire_days", "Survey links work for (days)", check=_between(1, 90)),
        Field(
            "survey_alert_at", "Follow up answers at or below",
            "1 to 4: such an answer adds an internal note to the ticket and emails the technician who handled it and the queue's managers, so someone can call the person. 0: no follow-up.",
            check=_between(0, 4),
        ),
        Field("survey_question", "The question", "Answered from 1 (very unhappy) to 5 (very happy), with an optional comment.", check=_length(5, 300)),
    ],
    "nutanix": [
        Field("nutanix_enabled", "Read Nutanix", "Optional: Layer 8 reads VMs, clusters and hosts from Prism Central (v4 API, pc.2024.1 or later) and matches VMs to devices by name. Technicians can link VMs to tickets. Turning it off stops reading and hides cached data."),
        Field("nutanix_url", "Prism Central address", "e.g. https://prism.corp.example:9440. Layer 8's server connects to it directly over HTTPS.", check=_prism_url),
        Field("nutanix_username", "Login", "A Prism Central account. For reading only, a role that can view VMs, clusters and hosts; power actions also need VM power rights.", check=_length(0, 128)),
        Field("nutanix_password", "Password", "Written to a secret file and never shown back.", secret=True, check=_length(0, 256)),
        Field("nutanix_ca_file", "Prism Central CA certificate", "The CA that issued Prism Central's certificate, in PEM form, when it isn't publicly trusted, or Prism Central's own self-signed certificate (then exactly that certificate is trusted, whatever address you use). Empty: the server's trusted CAs.", check=_certificates, file="nutanix-ca.pem"),
        Field("nutanix_verify_tls", "Check Prism Central's certificate", "Leave on. Turn off only to try Prism Central with its self-signed certificate on a network you trust."),
        Field("nutanix_sync_minutes", "Read Nutanix every (minutes)", "A failed read keeps the last complete one; older data is marked stale.", check=_between(5, 1440)),
        Field("nutanix_power_enabled", "Power actions from tickets", "Lets people with the permissions restart, power on or power-cycle a VM linked to a ticket (a write to Nutanix). Every action is noted on the ticket with who asked and how it ended."),
        Field("nutanix_critical_vms", "Critical VMs", "VM names, one per line (not case-sensitive), at most 200. Each is followed: not running, or gone from Prism Central, counts as down.", check=_vm_names),
        Field("nutanix_outage_tickets", "Open tickets for critical VMs that are down", "A critical VM down for the time below gets one high-priority ticket; when it runs again, an internal note is added for review (the ticket isn't closed). Follows each read."),
        Field("nutanix_outage_minutes", "Down for (minutes) before a ticket", "Rides out planned restarts. A VM shut down on purpose still gets a ticket after this long.", check=_between(1, 1440)),
        Field("nutanix_outage_queue_id", "Queue for outage tickets (ID)", "0: the default queue. Routing rules still apply.", check=_between(0, 2_000_000_000)),
        Field("nutanix_wallboard", "Show critical VMs on remote displays", "A tile with how many critical VMs are down, and an alert naming each one. Off by default: remote displays are often on screens anyone walking by can read."),
    ],
    "unifi": [
        Field("unifi_enabled", "Read UniFi networks", "Optional, read only: clients (MAC, IP, access point or switch, network) on IP records, devices and tickets, and UniFi gateways, switches and access points with their state. Turning it off stops reading and hides cached data."),
        Field("unifi_mode", "Connection", "Cloud: the Site Manager API at api.ui.com, reaching each console through Ubiquiti's Connector Proxy (console firmware 5.0.3 or later); no access to your LAN needed. Local: one UniFi OS console's own Network Integration API (UniFi Network 9 or later).", choices=(("cloud", "Cloud (api.ui.com)"), ("local", "Local console"))),
        Field("unifi_api_key", "API key", "Cloud: create it at unifi.ui.com > API. Local: UniFi Network > Settings > Integrations. Use a read-only admin where you can. Written to a secret file and never shown back.", secret=True, check=_length(0, 512)),
        Field("unifi_console_ids", "Consoles (cloud)", "Required in cloud mode: the console (host) IDs to read, one per line, at most 50. Empty reads nothing. Test connection lists the consoles the key can see.", check=_unifi_ids),
        Field("unifi_console_url", "Console address (local)", "Local mode: the console's HTTPS address, e.g. https://unifi.corp.example.", check=_https_url),
        Field("unifi_ca_file", "Console CA certificate (local)", "The CA that issued the console's certificate, in PEM form, when it isn't publicly trusted, or the console's own self-signed certificate, as UniFi consoles use out of the box (then exactly that certificate is trusted, whatever address you use). Empty: the server's trusted CAs.", check=_certificates, file="unifi-ca.pem"),
        Field("unifi_site_ids", "Sites", "Optional: only these site IDs, one per line. Empty: every site on the selected consoles.", check=_unifi_ids),
        Field("unifi_sync_minutes", "Read UniFi every (minutes)", "A failed read keeps the last complete snapshot; older data is marked stale.", check=_between(5, 1440)),
        Field("unifi_outage_tickets", "Open tickets for offline UniFi devices", "A gateway, switch or access point offline for the time below gets one ticket; when it comes back, an internal note is added for review (the ticket isn't closed). UniFi's official API has no alerts to push, so this follows each read."),
        Field("unifi_outage_minutes", "Offline for (minutes) before a ticket", "Rides out reboots and firmware updates.", check=_between(1, 1440)),
        Field("unifi_outage_queue_id", "Queue for outage tickets (ID)", "0: the default queue. Routing rules still apply.", check=_between(0, 2_000_000_000)),
        Field("unifi_vouchers_enabled", "Guest Wi-Fi vouchers from tickets", "Lets people with the permission make UniFi hotspot vouchers from a ticket (a write to UniFi). Logged on the ticket."),
    ],
    "junos": [
        Field("junos_enabled", "Read Juniper switches", "Optional, read only: a relay on your LAN reads each switch's Junos REST API for its MAC table, ARP table and identity. Clients show beside IP records, devices and tickets; switches show under IP addresses > Juniper."),
        Field("junos_switches", "Switches", "One per line: a name, then its address and, if not the default 3443, the REST port, e.g. core-1 10.0.0.2 or idf2-sw 10.0.2.2:8443. At most 100. Enable the API on each switch: set system services rest https port 3443 (with a server certificate).", multiline=True, check=_junos_switches),
        Field("junos_username", "Login", "A Junos login with a read-only class (e.g. read-only) on every switch.", check=_length(0, 64)),
        Field("junos_password", "Password", "Sealed to the relay for each read and never shown back.", secret=True, check=_length(0, 256)),
        Field("junos_ca_file", "Switches' CA certificate", "The CA that issued the switches' REST certificates, in PEM form, when it isn't publicly trusted.", check=_certificates, file="junos-ca.pem"),
        Field("junos_verify_tls", "Check the switches' certificates", "Leave on. Turn off only to try a switch with a self-signed certificate on a network you trust."),
        Field("junos_runner", "Relay", "The relay that reaches the switches (Python, relay 1.2 or later). Empty: any relay that takes Python.", check=_length(0, 100)),
        Field("junos_sync_minutes", "Read every (minutes)", "", check=_between(5, 1440)),
        Field("junos_uplink_macs", "A port with this many MACs is an uplink", "Uplinks see every client; they're skipped so each client shows on its access port.", check=_between(2, 10000)),
        Field("junos_outage_tickets", "Open tickets for unreachable switches", "A switch the relay can't read for the time below gets one ticket; when it answers again, an internal note is added for review."),
        Field("junos_outage_minutes", "Unreachable for (minutes) before a ticket", "", check=_between(1, 1440)),
        Field("junos_outage_queue_id", "Queue for outage tickets (ID)", "0: the default queue.", check=_between(0, 2_000_000_000)),
    ],
    "meraki": [
        Field("meraki_webhook_enabled", "Create tickets from Meraki device outages", "Opt in to stopped_reporting / started_reporting webhooks. Uses the selected network IDs below, independently of discovery. Recovery adds an internal note; it never closes a ticket."),
        Field("meraki_webhook_secret", "Webhook shared secret", "Use a separate random secret of at least 32 characters in the default Meraki payload. Stored in a secret file and never shown back.", secret=True, check=lambda v: _length(32, 256)(v) if v else None),
        Field("meraki_webhook_organization_ids", "Webhook organization IDs", "Required for webhooks: at most 20 organization IDs, one per line. Empty accepts nothing.", check=_meraki_networks),
        Field("meraki_webhook_queue_id", "Webhook fallback queue ID", "0 uses the default queue. Normal ticket routing rules still apply. A nonzero ID must identify an active queue.", check=_between(0, 2147483647)),
        Field("meraki_enabled", "Read Meraki client observations", "Optional, read only. Turning this off stops discovery and hides cached observations."),
        Field("meraki_diagnostics_enabled", "Allow read-only network diagnostics", "Technicians may explicitly read last-known switch port status or recent wireless events from the Meraki client view. OAuth requires switch:telemetry:read and wireless:telemetry:read in addition to discovery scopes; update the app registration and reconnect after changing this option."),
        Field("meraki_auth_mode", "Authentication", choices=(("api_key", "Dashboard API key"), ("oauth", "OAuth connection"))),
        Field("meraki_client_id", "OAuth client ID", "Register a confidential application at integrate.cisco.com. The exact redirect URI is shown above after saving.", check=_length(0, 256)),
        Field("meraki_client_secret", "OAuth client secret", "Written to a secret file and never shown back. Changing the registration requires reconnecting.", secret=True, check=_length(0, 1024)),
        Field("meraki_api_key", "Dashboard API key", "Use a dedicated Dashboard administrator with read-only access to the selected networks. This key is written to a secret file and never shown back.", secret=True, check=_length(0, 256)),
        Field("meraki_network_ids", "Selected Meraki networks", "Required for discovery and webhook intake: one network ID per line, at most 20. Empty reads and accepts nothing. Test selected networks after saving.", check=_meraki_networks),
        Field("meraki_sync_minutes", "Read Meraki every (minutes)", "Reads the last 24 hours of clients. Failed reads keep the last complete snapshot; older observations are marked stale.", check=_between(15, 1440)),
    ],
    "action1": [
        Field(
            "action1_region",
            "Action1 region",
            "Where your Action1 console is: app.action1.com is North America.",
            choices=(("NorthAmerica", "North America (app.action1.com)"), ("NorthAmerica-2", "North America 2 (app.na-2.action1.com)"), ("Europe", "Europe (app.eu.action1.com)"), ("UnitedKingdom", "United Kingdom (app.uk.action1.com)"), ("Australia", "Australia (app.au.action1.com)")),
        ),
        Field("action1_client_id", "Client ID", "From Action1 > Configuration > API credentials. A read-only role is enough. Empty: Action1 is off.", check=_length(0, 200)),
        Field("action1_client_secret", "Client secret", "Shown once by Action1 when the credentials are made.", secret=True),
        Field("action1_org_ids", "Organizations to read", "Empty: every organization these credentials can see, including ones added later. Or one organization ID per line to read only those (Test connection lists them).", check=_org_ids),
        Field("action1_sync_minutes", "Read Action1 every (minutes)", "Action1 asks for fewer than 30 requests a minute; one read is one request per 50 machines.", check=_between(15, 1440)),
        Field("action1_critical_days", "Open a ticket when critical updates are missing for (days)", "Per machine, from when a sync first saw them missing. 0: no tickets. Tickets go through the routing rules like any other.", check=_between(0, 365)),
        Field("action1_reboot_days", "Open a ticket when a reboot is pending for (days)", "0: no tickets.", check=_between(0, 365)),
        Field("action1_change_days", "Put patch automations on the change calendar (days ahead)", "Each enabled Action1 automation that deploys updates, runs an update ring or reboots becomes a pre-approved standard change when its next run is this close; its outcome comes from Action1 after the run. 0: no changes.", check=_between(0, 60)),
        Field("action1_change_hours", "A patch change lasts (hours)", "Its window on the calendar, for overlap warnings with other changes.", check=_between(1, 48)),
        Field("action1_deploy_enabled", "Allow reviewed software deployments", "Opt-in, separate from read-only patching. Requires separate credentials, an explicit organisation allowlist and the Action1 deployment permission."),
        Field("action1_deploy_client_id", "Deployment client ID", "Separate Action1 API credentials with view_endpoints, view_software_repository, view_automations and run_automations. Existing patch credentials stay read-only.", check=_length(0, 200)),
        Field("action1_deploy_client_secret", "Deployment client secret", "Stored as a write-only secret. Never included in previews or tickets.", secret=True),
        Field("action1_deploy_org_ids", "Organizations allowed for deployment", "One ID per line. Empty permits no deployments. Refresh patch status after upgrading to record each endpoint's organisation ID.", check=_org_ids),
    ],
    "ipam": [
        Field("ipam_stale_days", "Flag addresses not seen for (days)", "A documented address that scans saw before but haven't seen for this long is flagged. Devices that ignore ping are still seen when the relay is on their LAN (ARP).", check=_between(1, 3650)),
        Field("ipam_flag_pool_hosts", "Flag unknown hosts in DHCP pools too", "Off: hosts answering inside a subnet's DHCP pool are taken as leases and not flagged."),
        Field("ipam_dhcp_server", "Windows DHCP server", "Its name (e.g. dc01.corp.example). Scans then also read its scopes, reservations and leases for the subnet. Empty: not read.", check=_length(0, 255)),
        Field("ipam_dns_server", "Windows DNS server", "Its name. Scans then also read its A records for the subnet. Empty: not read.", check=_length(0, 255)),
        Field("ipam_dns_zones", "DNS zones to read", "Forward zones, separated by commas (e.g. corp.example). Empty: every primary forward zone on the server.", check=_length(0, 1000)),
        Field("ipam_windows_runner", "Relay for DHCP and DNS reads", "The relay's name: a Windows machine with the DhcpServer and DnsServer PowerShell modules (RSAT), running as an account in \"DHCP Users\" that can read the DNS zones. Empty: any relay that takes PowerShell.", check=_length(0, 100)),
        Field("snmp_communities", "SNMP communities (read-only, v2c)", "Separated by commas; each is tried in turn. Subnets then get Discover (SNMP): the subnet's relay asks every address for its name, model and serial, LLDP neighbours and switch MAC tables, so addresses and devices show the switch port they're on. Sent to the relay sealed, for that run only. Empty: off.", secret=True),
    ],
    "warranty": [
        Field("dell_client_id", "Dell TechDirect client ID", "From TechDirect (tdm.dell.com) > APIs, once Dell has approved the Warranty API for your company. Empty: Dell isn't looked up.", check=_length(0, 200)),
        Field("dell_client_secret", "Dell TechDirect client secret", "Shown with the client ID in TechDirect.", secret=True),
        Field("lenovo_client_id", "Lenovo ClientID", "The token Lenovo issues for its warranty API (ask your Lenovo account representative). Empty: Lenovo isn't looked up.", secret=True),
        Field("warranty_recheck_days", "Look each device up again after (days)", "New devices and ones with a new serial are looked up within a few hours.", check=_between(1, 365)),
    ],
    "rules": [
        Field("rules_statement_timeout_ms", "SQL rule time limit (ms)", "A SQL rule's query is stopped after this long.", check=_between(100, 300_000)),
    ],
}
FIELDS = {f.name: f for fields in SECTIONS.values() for f in fields}


def _secret_path(name: str):
    return settings.secrets_dir / name


def write_secret_file(path, data: bytes) -> None:
    """Write a file only this service can read, replacing any old one in one step."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# Stored values -> app.config.settings

_original: dict[str, Any] = {}  # values before anything was laid over them
_loaded_at = float("-inf")


def _stored(session: Session) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in session.scalars(select(AppSetting).where(AppSetting.key.in_(SECTIONS))):
        for f in SECTIONS[row.key]:
            if not f.secret and f.name in row.value:
                values[f.name] = row.value[f.name]
    for f in FIELDS.values():
        if f.secret and _secret_path(f.name).is_file():
            values[f.name] = _secret_path(f.name).read_text()
        if f.file and _secret_path(f.file).is_file():
            values[f.name] = str(_secret_path(f.file))
    return values


def refresh(session: Session, *, force: bool = False) -> None:
    """Lay stored values over settings (at most every RELOAD_SECONDS unless forced)."""
    global _loaded_at
    now = time.monotonic()
    if not force and now - _loaded_at < RELOAD_SECONDS:
        return
    _loaded_at = now
    values = {k: v for k, v in _stored(session).items() if k not in FROM_ENVIRONMENT}
    for name in list(_original):
        if name not in values:
            setattr(settings, name, _original.pop(name))
    for name, value in values.items():
        _original.setdefault(name, getattr(settings, name))
        setattr(settings, name, value)


def reset() -> None:
    """Undo every stored value laid over settings (tests)."""
    global _loaded_at
    for name, value in _original.items():
        setattr(settings, name, value)
    _original.clear()
    _loaded_at = float("-inf")


# Reading and changing a section


def describe(section: str) -> list[dict]:
    """The section's fields as the Settings screen shows them. Secrets: only whether set."""
    out = []
    for f in SECTIONS[section]:
        locked = f.name in FROM_ENVIRONMENT
        item = {"name": f.name, "label": f.label, "help": f.help, "kind": f.kind, "locked": locked}
        item["choices"] = [{"value": v, "label": label} for v, label in f.choices]
        if f.secret:
            item["is_set"] = bool(getattr(settings, f.name))
            item["value"] = None
        elif f.file:
            path = _secret_path(f.file)
            item["is_set"] = bool(getattr(settings, f.name))
            item["value"] = path.read_text() if path.is_file() else ""
        else:
            item["value"] = getattr(settings, f.name)
        out.append(item)
    return out


def update(session: Session, section: str, values: dict[str, Any], by: User | None, clear: list[str] = ()) -> None:
    """Store new values for some of a section's fields (all checked first); clear resets
    fields to their defaults. Secrets are written to their files."""
    if section not in SECTIONS:
        raise SettingsError(f"Unknown settings section {section!r}")
    known = {f.name: f for f in SECTIONS[section]}
    for name in [*values, *clear]:
        if name not in known:
            raise SettingsError(f"{name} isn't a {section} setting")
        if name in FROM_ENVIRONMENT:
            raise SettingsError(f"{known[name].label} is set on the server (HELPDESK_{name.upper()}); change it there")
    parsed = {name: known[name].parse(value) for name, value in values.items()}

    row = session.get(AppSetting, section)
    if row is None:
        row = AppSetting(key=section, value={})
        session.add(row)
    stored = dict(row.value)
    for name, value in parsed.items():
        f = known[name]
        if f.secret:
            write_secret_file(_secret_path(name), value.encode())
        elif f.file and value:
            write_secret_file(_secret_path(f.file), (value + "\n").encode())
        elif f.file:
            _secret_path(f.file).unlink(missing_ok=True)
        else:
            stored[name] = value
    for name in clear:
        stored.pop(name, None)
        if known[name].secret:
            _secret_path(name).unlink(missing_ok=True)
        if known[name].file:
            _secret_path(known[name].file).unlink(missing_ok=True)
    row.value = stored
    row.updated_at = datetime.now().astimezone()
    row.updated_by_id = by.id if by else None
    session.flush()
    refresh(session, force=True)
    if "ticket_first_number" in parsed:
        from app.numbering import apply_first_number

        apply_first_number(session)
    if "po_first_number" in parsed:
        from app.numbering import apply_first_number

        apply_first_number(session, "purchase_orders", settings.po_first_number)
    # Names only, never values (some are passwords).
    changed = [known[n].label for n in [*parsed, *clear]]
    log.info("%s settings changed by %s: %s", section.capitalize(), by.display_name if by else "the installer", ", ".join(changed))
