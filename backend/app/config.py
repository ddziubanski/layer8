from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from HELPDESK_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="HELPDESK_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk"
    storage_dir: Path = Path("./storage")
    # Built agent UI (frontend/dist). Served at / when it exists.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    # Development only: trust an X-Dev-User header holding an email, no password.
    # Never enable in production.
    dev_auth: bool = False
    # Signed-in sessions end after this long without use.
    session_hours: int = 12
    # Send the session cookie over HTTPS only. Turn off only for local development over http.
    cookie_secure: bool = True
    # Per-file cap for uploads from the portal and agent UI.
    max_upload_mb: int = 25

    # Microsoft Graph app registration for the intake mailbox (Exchange Online).
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    # Optional Teams digest. Configstore keeps the webhook as a write-only secret.
    teams_chat_enabled: bool = False
    teams_tenant_id: str = ""
    teams_client_id: str = ""
    teams_client_secret: str = ""
    # Microsoft 365 Copilot for technicians on tickets (Graph beta Copilot Chat API), through their own connection.
    copilot_enabled: bool = False
    teams_enabled: bool = False
    teams_webhook_url: str = ""
    teams_summary_minutes: int = 60
    # Opening hours (app.hours): the desk's, unless a queue has its own; shown on the portal.
    desk_hours: str = "Mon-Fri 08:00-17:00"
    desk_closed_days: str = ""
    after_hours_message: str = "No one is in right now. A technician is next expected {next}."
    # Technicians' Teams status (status only) on the portal, read through the Email tab's app registration.
    presence_on_portal: bool = False
    # Action1 (patching, read-only): API credentials from Action1 > API credentials, the organization
    # to read, and the region its console is in.
    action1_region: str = "NorthAmerica"
    action1_client_id: str = ""
    action1_client_secret: str = ""
    # Explicitly opt in with separate write-capable credentials and organisation allowlist.
    action1_deploy_enabled: bool = False
    action1_deploy_client_id: str = ""
    action1_deploy_client_secret: str = ""
    action1_deploy_org_ids: list[str] = []
    # Whether Action1 is in use at all (asked at install): off hides Settings > Action1 and patch
    # status everywhere, and nothing is read.
    action1_enabled: bool = False
    # SQL rules, reports and library queries (read the database directly, past restricted queues): off
    # until an admin accepts that (asked at install).
    sql_enabled: bool = False
    # Every machine in these organizations is read (e.g. one for servers, one for workstations).
    action1_org_ids: list[str] = []
    action1_sync_minutes: int = 60
    # Open a ticket when critical updates have been missing, or a reboot pending, this long (0: never).
    action1_critical_days: int = 0
    action1_reboot_days: int = 0
    # Patch automations (deploy updates, update rings, reboots) due within this many days become
    # pre-approved changes on the change calendar (0: none), each lasting this many hours.
    action1_change_days: int = 7
    action1_change_hours: int = 4
    # The shared mailbox tickets are emailed to and sent from.
    mailbox: str = ""
    # Senders from these domains become requesters automatically, as a JSON
    # list: ["corp.local", "example.com"]. Everyone else is rejected.
    mail_allowed_domains: list[str] = []
    mail_poll_seconds: int = 30
    # Linked from outgoing emails when set.
    portal_url: str = ""

    # AD / LDAP directory. Empty ldap_url: no directory, local accounts only.
    # One or more servers, comma-separated, tried in order: ldaps://dc1.corp.local,ldaps://dc2.corp.local
    ldap_url: str = ""
    # "ad" (Active Directory: objectGUID, proxyAddresses, nested groups via the AD matching rule)
    # or "generic" (other LDAP: entryUUID, mail, nested groups by walking member).
    ldap_kind: str = "ad"
    # Read-only service account used to search and sync.
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    # Which accounts are synced. Default: enabled-or-not people with a mailbox or UPN.
    ldap_user_filter: str = "(&(objectCategory=person)(objectClass=user)(|(mail=*)(userPrincipalName=*)))"
    # Where to search for groups; defaults to ldap_base_dn.
    ldap_group_base_dn: str = ""
    # Computers become devices (AD only). Empty filter: computers aren't synced.
    ldap_computer_base_dn: str = ""
    ldap_computer_filter: str = "(objectCategory=computer)"
    # AD changes from tickets (group membership, OU moves), done by a runner's PowerShell:
    # only groups under these OUs, and moves only into OUs under these.
    ad_change_group_bases: list[str] = []
    ad_change_ou_bases: list[str] = []
    ad_runner_name: str = ""
    # Lists of groups offered as tick boxes, "Label: name prefix" per line (e.g. "Distribution lists: [EMAIL]").
    ad_group_lists: list[str] = []
    # New AD accounts from tickets (onboarding): where they go, how they're named, first password.
    ad_new_user_ou: str = ""
    ad_new_user_domain: str = ""
    ad_new_user_name_format: str = "{f}{last}"
    ad_new_user_password_length: int = 16
    ad_new_user_enabled: bool = True
    ad_new_user_change_password: bool = True
    ad_new_user_proxy_address: bool = True
    # Who may create accounts is a permission (app.permissions: ad_create_accounts).
    # CA bundle for LDAPS certificate checks; empty uses the system store.
    ldap_ca_file: str = ""
    ldap_start_tls: bool = False
    # Plain ldap:// without TLS sends passwords in the clear. Refused unless set (lab use only).
    ldap_allow_insecure: bool = False
    ldap_timeout_seconds: int = 10
    ldap_sync_minutes: int = 30

    # Ticket numbers (Settings > General): where they start and how they're shown.
    ticket_first_number: int = 1000
    ticket_label_format: str = "#{n}"
    # Tickets Anywhere: technicians' #commands in email act on tickets.
    email_commands: bool = True
    # Only run them when Exchange Online vouches the mail came from inside the organization
    # (X-MS-Exchange-Organization-AuthAs: Internal), so a forged From can't use them.
    email_commands_internal_only: bool = True
    # Purchase order numbers, the same way.
    po_first_number: int = 1
    po_label_format: str = "PO-{n:05}"
    # The public receiving page (/receive): on/off, and who else hears when something arrives.
    public_receiving: bool = True
    receiving_notify: str = ""
    # Receiving (the dock / clerk): told what's coming when a PO is ordered.
    receiving_email: str = ""
    receiving_heads_up: bool = True

    # Branding (Settings > Branding). Images are uploaded there.
    product_name: str = "Layer 8"
    sign_in_message: str = ""
    background_in_console: bool = False
    portal_welcome: str = ""
    email_footer: str = ""
    # Theme: buttons and links, and the sidebar; dark mode off / auto (each device's setting) / on.
    theme_brand: str = "#0f6b5c"
    theme_sidebar: str = "#14211e"
    dark_mode: str = "off"

    # Backups (Settings > Backup): a folder on a mounted share, a daily time, how many to keep.
    backup_dir: str = ""
    backup_time: str = "02:00"
    backup_keep: int = 14
    backup_passphrase: str = ""

    # Ticket fields (Settings > Ticket fields).
    require_resolution_type: bool = True
    require_closure_reason: bool = True
    # A parent ticket stays open while any of its child tickets is.
    block_parent_close: bool = True
    # A requester's AD department not yet on the Departments list is added when a ticket uses it.
    departments_from_ad: bool = True

    # Server-only (never editable in the app): a command the API runs to force-restart a stuck
    # worker, e.g. "sudo -n systemctl restart helpdesk-worker" (the installer adds the sudo rule).
    # Empty: only the normal restart, which the worker does itself.
    worker_restart_command: str = ""

    # Files the app keeps secret, e.g. the Windows sign-in keytab uploaded in Settings.
    # Only the API service account should be able to read it.
    secrets_dir: Path = Path("./secrets")

    # The restricted database login SQL ticket rules run as (see app/rules/sqlmode.py).
    # Empty disables SQL rules; builder rules still work.
    rules_database_url: str = ""
    rules_statement_timeout_ms: int = 10_000
    # Changes: their label, the risk at which a normal change needs approval, and who approves
    # (members of this group, local or synced; empty: admins).
    change_label_format: str = "CHG-{n:05}"
    change_approval_level: str = "medium"
    change_approvers_group: str = ""
    # Device sweeps: stale after this many days without a sign of life; unknown things count when
    # seen in the last sweep_recent_days; a weekly summary ticket if on.
    sweep_stale_days: int = 90
    sweep_recent_days: int = 7
    sweep_weekly_ticket: bool = False
    # Satisfaction surveys: this percentage of solved tickets (picked at random) ask the requester how
    # it went (0: off); nobody is asked more than once in survey_cooldown_days; links last survey_expire_days.
    survey_percent: int = 0
    survey_cooldown_days: int = 30
    survey_expire_days: int = 14
    survey_question: str = "How happy are you with how we handled this request?"
    # An answer at or below this (1-4) adds an internal note and tells the technician and the queue's managers (0: never).
    survey_alert_at: int = 2
    # Lifecycle: a device is due for replacement this many years after purchase (unless it says
    # otherwise); warranties ending within warranty_warn_days are flagged.
    device_life_years: int = 5
    warranty_warn_days: int = 60
    # IPAM scans: a documented address not seen for this many days is flagged.
    ipam_stale_days: int = 30
    # Flag unknown hosts inside a subnet's DHCP pool too (normally they're just leases).
    ipam_flag_pool_hosts: bool = False
    # DHCP and DNS reads (Windows Server, through a runner with PowerShell and RSAT): the servers'
    # names (empty: not read), the forward zones (empty: every primary forward zone), and the runner
    # by name (empty: any runner that takes PowerShell).
    ipam_dhcp_server: str = ""
    ipam_dns_server: str = ""
    ipam_dns_zones: str = ""
    ipam_windows_runner: str = ""
    # Warranty lookups with the makers' own APIs: Dell TechDirect (client ID and secret) and
    # Lenovo (a ClientID token from Lenovo). Devices are looked up again after warranty_recheck_days.
    dell_client_id: str = ""
    dell_client_secret: str = ""
    lenovo_client_id: str = ""
    warranty_recheck_days: int = 30
    # SNMP discovery (v2c, read-only): community strings separated by commas (a secret; empty: off).
    snmp_communities: str = ""


settings = Settings()
