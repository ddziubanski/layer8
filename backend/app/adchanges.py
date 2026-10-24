# SPDX-License-Identifier: AGPL-3.0-or-later
"""Changing a ticket requester's AD account from the ticket: security groups, OU, department and
title, by hand or from an onboarding template.

Layer 8 itself only reads AD. The change runs as a built-in PowerShell action script on a runner
(ActiveDirectory module, the runner's service account needs the rights), so it is queued, maybe
approved (if an admin marks that script high risk), logged on the ticket and shown with its
output like any script run. Only groups under Settings > Directory's group OUs, and moves into
its allowed OUs, are accepted.

Hybrid sites: Entra ID doesn't follow nested groups for group-based licensing, app assignment or
Conditional Access, so groups that contain groups or sit inside one are flagged.
"""

import json
from datetime import date, datetime, time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import runs
from app.config import settings
from app.directory.sync import get_directory
from app.directory.types import DirectoryError
from app.models import OnboardingTemplate, RunStatus, Runner, Script, ScriptKind, ScriptLanguage, ScriptRun, ScriptVersion, Ticket, User

SCRIPT_NAME = "Layer 8: change an AD account"
NESTED_WARNING = "Nested group: Entra ID won't apply it through nesting (licensing, app assignment, Conditional Access). Add people directly."
SCRIPT_BODY = r"""# Built in: changes the ticket requester's AD account (Layer 8 > ticket > AD account).
# Runs on a runner with the ActiveDirectory module; its account needs rights on these groups and OUs.
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity ([guid]$user_guid) -ErrorAction Stop
"Account: $($u.SamAccountName) ($($u.DistinguishedName))"
foreach ($g in @(ConvertFrom-Json $add_groups)) {
  if ($g) { Add-ADGroupMember -Identity $g -Members $u -ErrorAction Stop; "Added to: $g" }
}
foreach ($g in @(ConvertFrom-Json $remove_groups)) {
  if ($g) { Remove-ADGroupMember -Identity $g -Members $u -Confirm:$false -ErrorAction Stop; "Removed from: $g" }
}
$set = @{}
if ($department) { $set.Department = $department }
if ($title) { $set.Title = $title }
if ($set.Count) { Set-ADUser -Identity $u @set -ErrorAction Stop; "Set: " + (($set.GetEnumerator() | ForEach-Object { "$($_.Key) = $($_.Value)" }) -join "; ") }
if ($move_to_ou) { Move-ADObject -Identity $u.DistinguishedName -TargetPath $move_to_ou -ErrorAction Stop; "Moved to: $move_to_ou" }
"Done."
"""
PARAMETERS = [
    {"name": "user_guid", "label": "User (objectGUID)", "type": "text", "required": True, "default": None, "choices": []},
    {"name": "add_groups", "label": "Add to groups (JSON list of DNs)", "type": "text", "required": False, "default": "[]", "choices": []},
    {"name": "remove_groups", "label": "Remove from groups (JSON list of DNs)", "type": "text", "required": False, "default": "[]", "choices": []},
    {"name": "move_to_ou", "label": "Move to OU (DN)", "type": "text", "required": False, "default": None, "choices": []},
    {"name": "department", "label": "Department", "type": "text", "required": False, "default": None, "choices": []},
    {"name": "title", "label": "Job title", "type": "text", "required": False, "default": None, "choices": []},
]


class ADChangeError(ValueError):
    """The message is safe to show."""


def under(dn: str, bases: list[str]) -> bool:
    d = dn.strip().lower()
    return any(d == b.strip().lower() or d.endswith("," + b.strip().lower()) for b in bases if b.strip())


def parent_ou(dn: str) -> str:
    return dn.split(",", 1)[1] if "," in dn else ""


def directory():
    d = get_directory()
    if d is None:
        raise ADChangeError("No directory is set up (Settings > Directory)")
    return d


def enabled() -> bool:
    return bool(settings.ldap_url) and bool(group_bases() or settings.ad_change_ou_bases or settings.ad_new_user_ou)


def ensure_script(session: Session) -> Script:
    """The built-in action script (made once; an admin may then mark it high risk, or limit it to admins)."""
    script = session.scalar(select(Script).where(Script.name == SCRIPT_NAME))
    if script is None:
        script = Script(
            name=SCRIPT_NAME, kind=ScriptKind.action, language=ScriptLanguage.powershell, agents_can_run=True, current_version=1,
            description="Built in: used by the AD account card on tickets and by onboarding templates. Mark it high risk to require approval.",
        )
        script.versions.append(ScriptVersion(number=1, language=script.language, body=SCRIPT_BODY, parameters=PARAMETERS, note="Built in"))
        session.add(script)
        session.flush()
    return script


def requester_account(ticket: Ticket) -> User:
    user = ticket.requester
    if not user.directory_id or not user.directory_dn:
        raise ADChangeError(f"{user.display_name} isn't an AD account here (a local account), so there's nothing to change in AD")
    return user


@dataclass
class GroupInfo:
    dn: str
    name: str
    changeable: bool = False
    nested: bool = False
    warning: str | None = None


@dataclass
class Plan:
    add: list[GroupInfo] = field(default_factory=list)
    remove: list[GroupInfo] = field(default_factory=list)
    already: list[GroupInfo] = field(default_factory=list)
    move_to_ou: str | None = None
    current_ou: str = ""
    department: str | None = None
    title: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.add or self.remove or self.move_to_ou or self.department or self.title)


def nesting(d, dn: str) -> tuple[bool, str | None]:
    """Whether the group has member groups or is inside another, and the warning to show."""
    try:
        nested = bool(d.group_children(dn)) or bool(d.direct_groups(dn))
    except DirectoryError:
        return False, None
    return nested, NESTED_WARNING if nested else None


def is_ou(value: str) -> bool:
    """A group list given as an OU (OU=Distribution Lists,DC=corp,DC=example) rather than a name prefix."""
    v = value.strip().upper()
    return v.startswith(("OU=", "CN=")) and ",DC=" in v


def list_ous() -> list[str]:
    """The OUs Settings > Directory's group lists point at."""
    return [prefix.strip() for label, _, prefix in (line.partition(":") for line in settings.ad_group_lists) if label.strip() and is_ou(prefix)]


def group_bases() -> list[str]:
    """Where groups technicians may change live: Settings > Directory's group OUs, plus any OU a
    group list points at (naming an OU for a list is saying its groups may be ticked)."""
    return [*settings.ad_change_group_bases, *list_ous()]


def group_lists() -> list[tuple[str, str, list]]:
    """Settings > Directory's group lists: (label, prefix or OU, groups technicians may change in it).
    A list is every group in an OU, or the groups whose names start with a prefix (e.g. [EMAIL])."""
    d = directory()
    out = []
    for line in settings.ad_group_lists:
        label, _, value = line.partition(":")
        label, value = label.strip(), value.strip()
        if not (label and value):
            continue
        try:
            if is_ou(value):
                groups = d.groups_named("", [value])
            elif settings.ad_change_group_bases:
                groups = [g for g in d.groups_named(value, settings.ad_change_group_bases) if under(g.dn, settings.ad_change_group_bases)]
            else:
                continue
        except DirectoryError as exc:
            raise ADChangeError(str(exc)) from None
        out.append((label, value, groups))
    return out


def current_groups(user: User) -> list[GroupInfo]:
    d = directory()
    try:
        groups = d.direct_groups(user.directory_dn)
    except DirectoryError as exc:
        raise ADChangeError(str(exc)) from None
    return [GroupInfo(g.dn, g.name, changeable=under(g.dn, group_bases())) for g in groups]


def _group(d, dn: str) -> GroupInfo:
    if not under(dn, group_bases()):
        raise ADChangeError(f"{dn} isn't under the OUs technicians may change (Settings > Directory)")
    nested, warning = nesting(d, dn)
    return GroupInfo(dn, dn.split(",", 1)[0].split("=", 1)[-1], True, nested, warning)


def plan(
    session: Session, ticket: Ticket, *, add: list[str], remove: list[str], move_to_ou: str | None,
    department: str | None, title: str | None, template: OnboardingTemplate | None = None,
) -> Plan:
    """What would change, checked against the allow-lists and the account as AD has it now."""
    user = requester_account(ticket)
    d = directory()
    have = {g.dn.lower(): g for g in current_groups(user)}
    if template is not None:
        add = [*add, *(g["dn"] for g in template.groups)]
        move_to_ou = move_to_ou or (template.ou_dn or None)
        department = department or (template.department or None)
        title = title or (template.title or None)
    p = Plan(current_ou=parent_ou(user.directory_dn), department=department or None, title=title or None)
    for dn in dict.fromkeys(a.strip() for a in add if a.strip()):
        if dn.lower() in have:
            p.already.append(have[dn.lower()])
        else:
            p.add.append(_group(d, dn))
    for dn in dict.fromkeys(r.strip() for r in remove if r.strip()):
        if dn.lower() not in have:
            raise ADChangeError(f"{user.display_name} isn't directly in {dn}")
        if not under(dn, group_bases()):
            raise ADChangeError(f"{dn} isn't under the OUs technicians may change (Settings > Directory)")
        p.remove.append(have[dn.lower()])
    if move_to_ou and move_to_ou.strip().lower() != p.current_ou.lower():
        if not under(move_to_ou, settings.ad_change_ou_bases):
            raise ADChangeError(f"{move_to_ou} isn't one of the OUs accounts may be moved into (Settings > Directory)")
        p.move_to_ou = move_to_ou.strip()
    return p


def runner_id(session: Session) -> int | None:
    name = settings.ad_runner_name.strip()
    if not name:
        return None
    runner = session.scalar(select(Runner).where(Runner.name == name, Runner.is_active))
    if runner is None or "powershell" not in runner.languages:
        raise runs.RunError(f"No active PowerShell runner called {name!r} (Settings > Directory)")
    return runner.id


def repin_pending(session: Session) -> int:
    """After Settings > Directory names a new AD runner: move AD jobs that haven't started (dated
    disables and enables among them) to it, so none wait for a host that won't take them."""
    rid = runner_id(session)
    if rid is None:
        return 0
    names = (SCRIPT_NAME, CREATE_SCRIPT_NAME, ENABLE_SCRIPT_NAME, DISABLE_SCRIPT_NAME)
    moved = 0
    for run in session.scalars(select(ScriptRun).join(Script, Script.id == ScriptRun.script_id).where(
        Script.name.in_(names), ScriptRun.status.in_((RunStatus.queued, RunStatus.awaiting_approval)),
    ).with_for_update(of=ScriptRun)):
        if run.only_runner_id != rid:
            run.only_runner_id = rid
            moved += 1
    return moved


def request(session: Session, ticket: Ticket, actor: User, p: Plan, template_name: str | None = None):
    """Queue the change on a PowerShell runner; it's logged on the ticket like any script run."""
    if p.empty:
        raise ADChangeError("Nothing to change")
    user = requester_account(ticket)
    script = ensure_script(session)
    values = {
        "user_guid": user.directory_id,
        "add_groups": json.dumps([g.dn for g in p.add]),
        "remove_groups": json.dumps([g.dn for g in p.remove]),
        "move_to_ou": p.move_to_ou or "",
        "department": p.department or "",
        "title": p.title or "",
    }
    try:
        made = runs.request(session, script, actor, device_ids=[], on_runner=True, values=values, ticket=ticket, only_runner_id=runner_id(session))
    except runs.RunError as exc:
        raise ADChangeError(str(exc)) from None
    what = []
    if template_name:
        what.append(f"template \"{template_name}\"")
    what += [f"add to {g.name}" for g in p.add] + [f"remove from {g.name}" for g in p.remove]
    if p.move_to_ou:
        what.append(f"move to {p.move_to_ou}")
    if p.department:
        what.append(f"department {p.department}")
    if p.title:
        what.append(f"title {p.title}")
    from app import services
    from app.models import TicketSource

    services.add_comment(
        session, ticket, actor, body=f"AD change for {user.display_name}: {'; '.join(what)}.", is_internal=True, source=TicketSource.agent, as_staff=True,
    )
    return made


# New accounts (onboarding): created on a runner like the changes above.

CREATE_SCRIPT_NAME = "Layer 8: create an AD account"
CREATE_BODY = r"""# Built in: creates an AD account from a ticket (Layer 8 > ticket > AD account > New account).
# Runs on a runner with the ActiveDirectory module; its account needs rights to create users in the OU
# and change the groups. The first password comes sealed from Layer 8 and is never printed.
Import-Module ActiveDirectory -ErrorAction Stop
if (-not $password) { throw "No password reached this runner (update the runner: it needs secret_values support)" }
$new = @{
  Name = $display_name; DisplayName = $display_name; GivenName = $given_name; Surname = $surname
  SamAccountName = $sam; UserPrincipalName = $upn; Path = $ou
  AccountPassword = (ConvertTo-SecureString $password -AsPlainText -Force)
  Enabled = [bool]$enabled; ChangePasswordAtLogon = [bool]$change_password
}
if ($mail) { $new.EmailAddress = $mail }
if ($phone) { $new.OfficePhone = $phone }
if ($mobile) { $new.MobilePhone = $mobile }
if ($department) { $new.Department = $department }
if ($title) { $new.Title = $title }
if ($office) { $new.Office = $office }
if ($company) { $new.Company = $company }
if ($description) { $new.Description = $description }
if ($manager_dn) { $new.Manager = $manager_dn }
$other = @{}
if ($extension) { $other.ipPhone = $extension }
if ($mail -and $proxy_address) { $other.proxyAddresses = "SMTP:$mail" }
if ($other.Count) { $new.OtherAttributes = $other }
New-ADUser @new -ErrorAction Stop
Remove-Variable password
$u = Get-ADUser -Identity $sam -ErrorAction Stop
"Created: $($u.SamAccountName) ($($u.UserPrincipalName)) in $ou"
foreach ($g in @(ConvertFrom-Json $groups)) {
  if ($g) { Add-ADGroupMember -Identity $g -Members $u -ErrorAction Stop; "Added to: $g" }
}
if (-not $enabled) { "The account is disabled until someone enables it." }
"Done."
"""
_T = {"type": "text", "required": False, "default": None, "choices": []}
CREATE_PARAMETERS = [
    {**_T, "name": "sam", "label": "Account name", "required": True},
    {**_T, "name": "upn", "label": "UPN", "required": True},
    {**_T, "name": "given_name", "label": "First name", "required": True},
    {**_T, "name": "surname", "label": "Last name", "required": True},
    {**_T, "name": "display_name", "label": "Display name", "required": True},
    {**_T, "name": "ou", "label": "OU (DN)", "required": True},
    {**_T, "name": "mail", "label": "Email"},
    {**_T, "name": "phone", "label": "Phone number"},
    {**_T, "name": "extension", "label": "Extension (ipPhone)"},
    {**_T, "name": "mobile", "label": "Mobile"},
    {**_T, "name": "department", "label": "Department"},
    {**_T, "name": "title", "label": "Job title"},
    {**_T, "name": "office", "label": "Office"},
    {**_T, "name": "company", "label": "Company"},
    {**_T, "name": "description", "label": "Description"},
    {**_T, "name": "manager_dn", "label": "Manager (DN)"},
    {**_T, "name": "groups", "label": "Groups (JSON list of DNs)", "default": "[]"},
    {"name": "enabled", "label": "Enabled", "type": "bool", "required": False, "default": True, "choices": []},
    {"name": "change_password", "label": "Must change password at first sign-in", "type": "bool", "required": False, "default": True, "choices": []},
    {"name": "proxy_address", "label": "Set proxyAddresses", "type": "bool", "required": False, "default": True, "choices": []},
]
MAX_SAM = 20  # sAMAccountName's limit for users
# No look-alikes (0/O, 1/l/I), so it can be read out or typed from a note.
PW_LOWER, PW_UPPER, PW_DIGITS, PW_SYMBOLS = "abcdefghjkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "!#%+=?@"


def _fold(text: str) -> str:
    """Lower-case ASCII letters and digits only: Zoë O'Brien-Smith -> zoeobriensmith."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c for c in plain.lower() if c.isalnum())


def account_name(fmt: str, first: str, last: str) -> str:
    f, l = _fold(first), _fold(last)
    try:
        name = fmt.format(first=f, last=l, f=f[:1], l=l[:1])
    except (KeyError, IndexError, ValueError) as exc:
        raise ADChangeError(f"The account name format can only use {{first}}, {{last}}, {{f}} and {{l}} ({exc})") from None
    name = "".join(c for c in name.lower() if c.isalnum() or c in "._-").strip("._-")
    if not name:
        raise ADChangeError("That account name format gives an empty name")
    return name[:MAX_SAM]


def new_password(length: int) -> str:
    import secrets

    pools = (PW_LOWER, PW_UPPER, PW_DIGITS, PW_SYMBOLS)
    chars = [secrets.choice(p) for p in pools] + [secrets.choice("".join(pools)) for _ in range(max(length, 12) - len(pools))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def create_script(session: Session) -> Script:
    script = session.scalar(select(Script).where(Script.name == CREATE_SCRIPT_NAME))
    if script is None:
        script = Script(
            name=CREATE_SCRIPT_NAME, kind=ScriptKind.action, language=ScriptLanguage.powershell, agents_can_run=True, current_version=1,
            description="Built in: used by New account on a ticket's AD account card. Mark it high risk to require approval.",
        )
        script.versions.append(ScriptVersion(number=1, language=script.language, body=CREATE_BODY, parameters=CREATE_PARAMETERS, note="Built in"))
        session.add(script)
        session.flush()
    return script


def configured() -> bool:
    return bool(settings.ldap_url) and bool(settings.ad_new_user_ou)


def not_allowed(session: Session, user: User) -> str | None:
    """Why this person may not create accounts (Settings > Permissions), or None."""
    from app import permissions

    return permissions.not_allowed(session, user, "ad_create_accounts")


ENABLE_SCRIPT_NAME = "Layer 8: enable an AD account"
ENABLE_BODY = r"""# Built in: enables a new starter's account on their start date (queued with the new account).
Import-Module ActiveDirectory -ErrorAction Stop
Enable-ADAccount -Identity $sam -ErrorAction Stop
"Enabled: $sam"
"""


def enable_script(session: Session) -> Script:
    script = session.scalar(select(Script).where(Script.name == ENABLE_SCRIPT_NAME))
    if script is None:
        script = Script(
            name=ENABLE_SCRIPT_NAME, kind=ScriptKind.action, language=ScriptLanguage.powershell, agents_can_run=True, current_version=1,
            description="Built in: enables a new account on its start date (New account on a ticket, with a start date).",
        )
        script.versions.append(ScriptVersion(number=1, language=script.language, body=ENABLE_BODY, parameters=[{**_T, "name": "sam", "label": "Account name", "required": True}], note="Built in"))
        session.add(script)
        session.flush()
    return script


DISABLE_SCRIPT_NAME = "Layer 8: disable an AD account"
DISABLE_BODY = r"""# Built in: disables a leaver's account on the day (an offboarding checklist's step).
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity ([guid]$user_guid) -ErrorAction Stop
Disable-ADAccount -Identity $u -ErrorAction Stop
"Disabled: $($u.SamAccountName) ($($u.DistinguishedName))"
"""


def disable_script(session: Session) -> Script:
    script = session.scalar(select(Script).where(Script.name == DISABLE_SCRIPT_NAME))
    if script is None:
        script = Script(
            name=DISABLE_SCRIPT_NAME, kind=ScriptKind.action, language=ScriptLanguage.powershell, agents_can_run=True, current_version=1,
            description="Built in: disables a leaver's AD account on the day (offboarding checklists). Mark it high risk to require approval.",
        )
        params = [{**_T, "name": "user_guid", "label": "User (objectGUID)", "required": True}]
        script.versions.append(ScriptVersion(number=1, language=script.language, body=DISABLE_BODY, parameters=params, note="Built in"))
        session.add(script)
        session.flush()
    return script


def _manager_email(ticket: Ticket, actor: User, a: "NewAccount") -> dict:
    """For the manager once the account exists: what it is, never the password."""
    from app import numbering

    when = f" It's enabled on {a.start_date:%A %d %B %Y}." if a.start_date is not None and not a.enabled else ""
    lines = [
        f"Hello {a.manager.display_name},",
        "",
        f"The account for {a.display_name} is ready.{when}",
        "",
        f"Account name: {a.sam}",
        f"Sign-in: {a.upn}",
    ]
    if a.mail:
        lines.append(f"Email: {a.mail}")
    lines += [
        "",
        f"The first password was given to {actor.display_name} at the service desk; ask them for it."
        + (" It has to be changed at the first sign-in." if a.change_password else ""),
        "",
        f"Ticket {numbering.label(ticket.id, ticket.created_at)}: {ticket.subject}",
    ]
    return {"to": a.manager.email, "subject": f"Account ready: {a.display_name} ({a.sam})", "body": "\n".join(lines)}


def can_create(session: Session, user: User) -> bool:
    return configured() and not_allowed(session, user) is None


@dataclass
class NewAccount:
    given_name: str
    surname: str
    display_name: str
    sam: str
    upn: str
    mail: str
    ou: str
    phone: str = ""
    extension: str = ""
    mobile: str = ""
    department: str = ""
    title: str = ""
    office: str = ""
    company: str = ""
    description: str = ""
    manager: User | None = None
    groups: list[GroupInfo] = field(default_factory=list)
    enabled: bool = True
    change_password: bool = True
    start_date: date | None = None
    email_manager: bool = False
    # Things that stop it (a name that's taken...); warnings don't.
    problems: list[str] = field(default_factory=list)


def plan_new(
    session: Session, *, given_name: str, surname: str, display_name: str = "", sam: str = "", mail: str = "", ou: str = "",
    phone: str = "", extension: str = "", mobile: str = "", department: str = "", title: str = "", office: str = "", company: str = "",
    description: str = "", manager_id: int | None = None, groups: list[str] = (), template: OnboardingTemplate | None = None,
    start_date: date | None = None, email_manager: bool = False,
) -> NewAccount:
    """The account as it would be created: names filled in (from Settings > Directory's format) and
    checked against AD, the template merged in, everything checked against the allow-lists."""
    if not configured():
        raise ADChangeError("Creating accounts isn't set up (Settings > Directory: where new accounts go)")
    given_name, surname = given_name.strip(), surname.strip()
    if not given_name or not surname:
        raise ADChangeError("Give the person's first and last name")
    d = directory()
    domain = settings.ad_new_user_domain.strip().lstrip("@")
    picked = sam.strip().lower()
    sam = picked or account_name(settings.ad_new_user_name_format, given_name, surname)
    if not picked:
        # The format's name is taken: try jsmith2, jsmith3...
        try:
            for n in range(2, 10):
                if not d.account_taken(sam=sam, upn=f"{sam}@{domain}", mail=""):
                    break
                sam = account_name(settings.ad_new_user_name_format, given_name, surname)[: MAX_SAM - 1] + str(n)
        except DirectoryError as exc:
            raise ADChangeError(str(exc)) from None
    if template is not None:
        groups = [*groups, *(g["dn"] for g in template.groups)]
        ou = ou or template.ou_dn
        department = department or template.department
        title = title or template.title
    a = NewAccount(
        given_name=given_name, surname=surname, display_name=display_name.strip() or f"{given_name} {surname}", sam=sam,
        upn=f"{sam}@{domain}" if domain else sam, mail=mail.strip(), ou=(ou or settings.ad_new_user_ou).strip(),
        phone=phone.strip(), extension=extension.strip(), mobile=mobile.strip(), department=(department or "").strip(), title=(title or "").strip(),
        office=office.strip(), company=company.strip(), description=description.strip(),
        enabled=settings.ad_new_user_enabled, change_password=settings.ad_new_user_change_password,
        start_date=start_date, email_manager=email_manager,
    )
    today = datetime.now().astimezone().date()
    if start_date is not None and start_date < today:
        a.problems.append("The start date has passed: leave it empty to create the account now")
    elif start_date is not None and start_date > today:
        # Disabled until the day: a run that waits for it enables it.
        a.enabled = False
    if not domain:
        a.problems.append("Set the new accounts' UPN domain in Settings > Directory")
    if len(sam) > MAX_SAM or not all(c.isalnum() or c in "._-" for c in sam):
        a.problems.append(f"The account name can be at most {MAX_SAM} letters, digits, dots, dashes or underscores")
    if a.mail and "@" not in a.mail:
        a.problems.append(f"{a.mail} isn't an email address")
    if not under(a.ou, [settings.ad_new_user_ou, *settings.ad_change_ou_bases]):
        a.problems.append(f"{a.ou} isn't an OU new accounts may go in (Settings > Directory)")
    if manager_id is not None:
        a.manager = session.get(User, manager_id)
        if a.manager is None or not a.manager.directory_dn:
            a.problems.append("The manager has to be someone from AD")
    if email_manager and (a.manager is None or not a.manager.email):
        a.problems.append("Pick the manager (from AD, with an email address) to email them")
    for dn in dict.fromkeys(g.strip() for g in groups if g.strip()):
        try:
            a.groups.append(_group(d, dn))
        except ADChangeError as exc:
            a.problems.append(str(exc))
    try:
        for what, dn in d.account_taken(sam=a.sam, upn=a.upn, mail=a.mail):
            a.problems.append(f"The {what} is already used by {dn}")
    except DirectoryError as exc:
        raise ADChangeError(str(exc)) from None
    return a


def create(session: Session, ticket: Ticket, actor: User, a: NewAccount, template_name: str | None = None):
    """Queue the new account on a PowerShell runner. Returns the runs and the first password, which is
    shown once to `actor` and kept nowhere (the runner gets it sealed, see app.runseal)."""
    if a.problems:
        raise ADChangeError("; ".join(a.problems))
    script = create_script(session)
    password = new_password(settings.ad_new_user_password_length)
    values = {
        "sam": a.sam, "upn": a.upn, "given_name": a.given_name, "surname": a.surname, "display_name": a.display_name, "ou": a.ou,
        "mail": a.mail, "phone": a.phone, "extension": a.extension, "mobile": a.mobile, "department": a.department, "title": a.title,
        "office": a.office, "company": a.company, "description": a.description, "manager_dn": a.manager.directory_dn if a.manager else "",
        "groups": json.dumps([g.dn for g in a.groups]), "enabled": a.enabled, "change_password": a.change_password,
        "proxy_address": settings.ad_new_user_proxy_address,
    }
    try:
        made = runs.request(
            session, script, actor, device_ids=[], on_runner=True, values=values, ticket=ticket, sealed={"password": password},
            on_success=_manager_email(ticket, actor, a) if a.email_manager else None, only_runner_id=runner_id(session),
        )
        if a.start_date is not None and not a.enabled:
            # From the start of that day, server time.
            at = datetime.combine(a.start_date, time.min).astimezone()
            made += runs.request(session, enable_script(session), actor, device_ids=[], on_runner=True, values={"sam": a.sam}, ticket=ticket, not_before=at, only_runner_id=runner_id(session))
    except runs.RunError as exc:
        raise ADChangeError(str(exc)) from None
    from app import services
    from app.models import TicketSource

    lines = [f"New AD account for {a.display_name}: {a.sam} ({a.upn}) in {a.ou}."]
    if template_name:
        lines.append(f"Template: {template_name}.")
    if a.groups:
        lines.append("Groups: " + ", ".join(g.name for g in a.groups) + ".")
    details = [f"email {a.mail}" if a.mail else "", f"phone {a.phone}" if a.phone else "", f"ext. {a.extension}" if a.extension else "",
               f"department {a.department}" if a.department else "", f"title {a.title}" if a.title else "",
               f"manager {a.manager.display_name}" if a.manager else ""]
    if any(details):
        lines.append("Details: " + ", ".join(x for x in details if x) + ".")
    lines.append(f"First password shown once to {actor.display_name}, not stored" + ("; it must be changed at first sign-in." if a.change_password else "."))
    if a.start_date is not None and not a.enabled:
        lines.append(f"Created disabled; it's enabled on {a.start_date:%A %d %B %Y} (run #{made[-1].id} waits until then).")
    elif not a.enabled:
        lines.append("Created disabled: enable it on the start date.")
    if a.email_manager:
        lines.append(f"{a.manager.display_name} is emailed once it's created (without the password).")
    services.add_comment(session, ticket, actor, body=" ".join(lines), is_internal=True, source=TicketSource.agent, as_staff=True)
    return made, password
