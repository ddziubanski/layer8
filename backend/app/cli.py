# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bootstrap commands, run on the server: python -m app.cli create-admin you@corp.local "Your Name" """

import argparse
import getpass
import json
import sys

from sqlalchemy import func, select

from pathlib import Path

from app import auth, configstore, kerberos
from app.db import SessionLocal
from app.models import Priority, Queue, RoutingRule, SlaPolicy, User, UserRole

# Starting point only; adjust through the API.
DEFAULT_SLAS = {
    Priority.critical: (15, 4 * 60),
    Priority.high: (60, 8 * 60),
    Priority.medium: (4 * 60, 3 * 24 * 60),
    Priority.low: (8 * 60, 5 * 24 * 60),
}


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise SystemExit("Passwords don't match")
    return first


def list_admins() -> None:
    """Local admins (break-glass candidates): they sign in with a password even when AD is down."""
    with SessionLocal() as session:
        rows = session.scalars(select(User).where(User.role == UserRole.admin).order_by(User.email)).all()
        for u in rows:
            kind = "local, has a password" if u.password_hash else ("directory" if u.directory_id else "local, no password")
            print(f"{u.email}\t{u.display_name}\t{kind}\t{'active' if u.is_active else 'disabled'}")
        if not rows:
            print("No admins.")


def sign_out_everyone() -> None:
    from app.models import UserSession

    with SessionLocal() as session:
        n = session.query(UserSession).delete()
        session.commit()
        print(f"Signed out {n} session(s)")


def create_admin(email: str, name: str, password: str) -> None:
    with SessionLocal() as session:
        user = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is None:
            user = User(email=email, display_name=name)
            session.add(user)
            session.flush()
        user.role = UserRole.admin
        user.is_active = True
        try:
            auth.set_password(session, user, password)
        except auth.AuthError as exc:
            raise SystemExit(str(exc)) from None
        session.commit()
        print(f"Admin ready: {user.email} (id {user.id})")


def set_password(email: str, password: str) -> None:
    with SessionLocal() as session:
        user = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is None:
            raise SystemExit(f"No user {email}")
        try:
            auth.set_password(session, user, password)
        except auth.AuthError as exc:
            raise SystemExit(str(exc)) from None
        session.commit()
        print(f"Password set for {user.email}; their other sessions were signed out")


def reset_two_factor(email: str) -> None:
    """Recovery from the server: someone (often the only admin) lost their authenticator app."""
    from app.api.auth import clear_two_factor

    with SessionLocal() as session:
        user = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is None:
            raise SystemExit(f"No user {email}")
        clear_two_factor(user)
        auth.end_sessions(session, user)
        session.commit()
        print(f"Two-factor sign-in turned off for {user.email}; they were signed out everywhere")


def seed(session=None) -> None:
    """Create a default "Service desk" queue and SLA policies if none exist (in the given session, which the caller commits; else its own)."""
    if session is None:
        with SessionLocal() as own:
            seed(own)
            own.commit()
        return
    if session.scalar(select(Queue).where(Queue.is_default)) is None:
        session.add(Queue(name="Service desk", is_default=True))
        print("Created default queue: Service desk")
    for priority, (response, resolution) in DEFAULT_SLAS.items():
        if session.scalar(select(SlaPolicy).where(SlaPolicy.priority == priority)) is None:
            session.add(SlaPolicy(priority=priority, first_response_minutes=response, resolution_minutes=resolution))
            print(f"Created SLA policy: {priority}")
    session.flush()


# A realistic starting configuration for trying the app out. Never run on a real install.
# IT's own teams: not offered in the portal directly, reached through IT's problem options.
DEMO_IT_TEAMS = ["Network", "Accounts", "Hardware", "Software", "Printing"]
# Departments in the portal: name, description, order, [(problem option, IT team it goes to or None)]
DEMO_DEPARTMENTS = [
    ("Information Technology", "Computers, phones, accounts, Wi-Fi, printers and software", 10, [
        ("Wi-Fi or network", "Network"), ("VPN or working from home", "Network"),
        ("Password or account", "Accounts"), ("Access to a folder or app", "Accounts"),
        ("Laptop, PC or monitor", "Hardware"), ("Phone or mobile", "Hardware"),
        ("Email, Outlook or Teams", "Software"), ("Install or error message", "Software"),
        ("Printer or scanner", "Printing"),
    ]),
    ("Human Resources", "Pay, leave, contracts, starters and leavers", 20, [
        ("Payroll or payslip", None), ("Holiday or leave", None), ("Sickness absence", None),
        ("New starter", None), ("Leaver", None), ("Benefits", None), ("Policy question", None),
    ]),
    ("Maintenance", "Building, heating, lighting, keys and furniture", 30, [
        ("Heating or air conditioning", None), ("Lighting or power", None), ("Plumbing or toilets", None),
        ("Doors, locks or keys", None), ("Furniture or office move", None), ("Cleaning", None), ("Car park", None),
    ]),
]


def seed_demo(session=None) -> None:
    """Demo departments with problem options (IT's routed to IT teams), plus two sample rules
    (in the given session, which the caller commits; else its own)."""
    if session is None:
        with SessionLocal() as own:
            seed_demo(own)
            own.commit()
        return
    seed(session)
    def queue(name: str) -> Queue:
        q = session.scalar(select(Queue).where(Queue.name == name))
        if q is None:
            q = Queue(name=name)
            session.add(q)
            session.flush()
            print(f"Created queue: {name}")
        return q

    desk = session.scalar(select(Queue).where(Queue.is_default))
    if not desk.portal_name:
        desk.show_in_portal = True
        desk.portal_name = "Something else / not sure"
        desk.portal_description = "We'll pass it to the right team"
        desk.portal_position = 90
    teams = {name: queue(name) for name in DEMO_IT_TEAMS}
    for team in teams.values():
        team.show_in_portal = False
    for name, description, position, options in DEMO_DEPARTMENTS:
        q = queue(name)
        if q.categories:
            # Never overwrite options someone has already set up.
            print(f"Left as is (already has problem options): {name}")
            continue
        q.show_in_portal, q.portal_description, q.portal_position = True, q.portal_description or description, position
        q.categories = [label for label, _ in options]
        q.category_routes = {label: teams[team].id for label, team in options if team}
    session.flush()
    it = queue("Information Technology")
    rules = [
        # Typed anywhere, with no department chosen: "password" goes to Accounts.
        RoutingRule(name="demo: password keyword", position=10, match_keywords=["password", "locked out"], set_queue_id=teams["Accounts"].id),
        # IT / VPN problems are urgent.
        RoutingRule(name="demo: VPN is high priority", position=20, match_requested_queue_id=it.id, match_category="VPN or working from home", set_priority=Priority.high),
    ]
    for rule in rules:
        if session.scalar(select(RoutingRule).where(RoutingRule.name == rule.name)) is None:
            session.add(rule)
    session.flush()
    print("Demo departments ready: Information Technology, Human Resources, Maintenance")


def _import_checklist(session, t: dict) -> str:
    """One checklist template from the answers file; matched by name, so importing again updates it."""
    from app.models import ChecklistTemplate
    from app.schemas import ChecklistStep, ChecklistTemplateIn

    name = str(t.get("name", "")).strip()
    if not name:
        raise configstore.SettingsError("checklist_templates: every checklist needs a name")
    steps = []
    for i, item in enumerate(t.get("items") or [], 1):
        item = dict(item)
        if q := item.pop("queue", None):
            queue = session.scalar(select(Queue).where(func.lower(Queue.name) == str(q).lower()))
            if queue is None:
                raise configstore.SettingsError(f"checklist_templates: {name}, step {i}: no queue {q!r}")
            item["queue_id"] = queue.id
        if a := item.pop("assignee", None):
            user = session.scalar(select(User).where(func.lower(User.email) == str(a).lower()))
            if user is None:
                raise configstore.SettingsError(f"checklist_templates: {name}, step {i}: no user {a!r}")
            item["assignee_id"] = user.id
        steps.append(ChecklistStep(**item))
    body = ChecklistTemplateIn(name=name, kind=t.get("kind", "other"), description=t.get("description", ""), items=steps)
    row = session.scalar(select(ChecklistTemplate).where(func.lower(ChecklistTemplate.name) == name.lower())) or ChecklistTemplate(name=name)
    row.kind, row.description, row.items = body.kind, body.description, [s.model_dump() for s in body.items]
    session.add(row)
    return name


def import_settings(session, answers: dict, base: Path = Path(".")) -> list[str]:
    """Apply an installer answers file: {"general": {"portal_url": ...}, ...,
    "onboarding_templates": [{"name": ..., "groups": ["CN=...", ...], "ou": "OU=...", "department": ..., "title": ...}],
    "checklist_templates": [{"name": ..., "kind": "onboarding", "items": [{"title": ..., "details": ..., "queue": "Desk",
        "assignee": "tech@corp.example", "offset_days": -3, "action": "disable_ad_account"}]}] (queues by name, technicians by email),
    "change_templates": [{"name": ..., "pre_approved": true, "implementation_plan": ..., "rollback_plan": ..., "impact": 1, "likelihood": 1}],
    "ticket_templates": [{"name": ..., "subject": ..., "description": ..., "category": ..., "priority": "high", "queue": "Desk",
        "checklist": "New starter"}] (queue and checklist by name; no requester or assignee),
    "layouts": {"ticket": {"top": [...], "main": [...], "side": [...], "bottom": [...], "hidden": [...]}} (everyone's default),
    "permissions": {"ticket_delete": {"who": "groups", "groups": ["IT Leads"]}, "kb_publish": {"who": "admins"}} (groups by name;
        they must exist: local, or synced from AD before the import),
    "windows_sign_in": {"keytab_file": "/path/http.keytab", "enabled": true, "silent": false}}.
    Any value can instead be {"file": "path"}: the file's text, e.g. a password or certificate
    (relative paths are from the answers file). Database changes are all or nothing (the caller
    commits); secret files are written as each section passes its checks. Returns what was done."""
    def resolve(value):
        if isinstance(value, dict) and set(value) == {"file"}:
            return (base / value["file"]).read_text().strip()
        return value

    answers = {k: ({n: resolve(v) for n, v in s.items()} if k in configstore.SECTIONS else s) for k, s in answers.items()}
    done = []
    windows = answers.get("windows_sign_in")
    unknown = set(answers) - set(configstore.SECTIONS) - {"windows_sign_in", "onboarding_templates", "checklist_templates", "change_templates", "ticket_templates", "layouts", "permissions"}
    if unknown:
        raise configstore.SettingsError(f"Unknown sections: {', '.join(sorted(unknown))}")
    rules = dict(answers.get("permissions") or {})
    # Older answer files said who may create AD accounts under directory.
    directory = answers.get("directory")
    if directory and ("ad_new_user_allowed" in directory or "ad_new_user_group" in directory):
        who, group = directory.pop("ad_new_user_allowed", "agents"), str(directory.pop("ad_new_user_group", "") or "").strip()
        rules.setdefault("ad_create_accounts", {"who": "groups", "groups": [group]} if who == "group" and group else {"who": "admins" if who != "agents" else "agents"})
    for section in configstore.SECTIONS:
        if section in answers:
            configstore.update(session, section, answers[section], None)
            done.append(f"{section}: {', '.join(answers[section])}")
    for t in answers.get("onboarding_templates") or []:
        # Matched by name: importing again updates them.
        from app.models import OnboardingTemplate

        name = str(t.get("name", "")).strip()
        if not name:
            raise configstore.SettingsError("onboarding_templates: every template needs a name")
        row = session.scalar(select(OnboardingTemplate).where(func.lower(OnboardingTemplate.name) == name.lower())) or OnboardingTemplate(name=name)
        row.description = t.get("description", "")
        row.groups = [{"dn": g, "name": g.split(",", 1)[0].split("=", 1)[-1]} if isinstance(g, str) else g for g in t.get("groups", [])]
        row.ou_dn, row.department, row.title = t.get("ou", ""), t.get("department", ""), t.get("title", "")
        session.add(row)
        done.append(f"onboarding template: {name}")
    for t in answers.get("checklist_templates") or []:
        done.append(f"checklist: {_import_checklist(session, t)}")
    for t in answers.get("change_templates") or []:
        from app.models import ChangeTemplate
        from app.schemas import ChangeTemplateIn

        body = ChangeTemplateIn(**t)
        row = session.scalar(select(ChangeTemplate).where(func.lower(ChangeTemplate.name) == body.name.strip().lower())) or ChangeTemplate()
        for f, v in body.model_dump().items():
            setattr(row, f, v)
        session.add(row)
        done.append(f"change template: {body.name}")
    for t in answers.get("ticket_templates") or []:
        from app.models import ChecklistTemplate, Queue, TicketTemplate
        from app.schemas import TicketTemplateIn
        from pydantic import ValidationError

        name = str(t.get("name", "")).strip()
        if not name:
            raise configstore.SettingsError("ticket_templates: every template needs a name")
        unknown_keys = set(t) - {"name", "subject", "description", "category", "priority", "queue", "checklist", "is_active"}
        if unknown_keys:
            raise configstore.SettingsError(f"ticket_templates: {name}: unknown fields {', '.join(sorted(unknown_keys))} (templates hold no requester or assignee)")
        queue_id = checklist_id = None
        if q := str(t.get("queue") or "").strip():
            queue_id = session.scalar(select(Queue.id).where(func.lower(Queue.name) == q.lower(), Queue.is_active))
            if queue_id is None:
                raise configstore.SettingsError(f"ticket_templates: {name}: no queue {q!r}")
        if c := str(t.get("checklist") or "").strip():
            checklist_id = session.scalar(select(ChecklistTemplate.id).where(func.lower(ChecklistTemplate.name) == c.lower()))
            if checklist_id is None:
                raise configstore.SettingsError(f"ticket_templates: {name}: no checklist {c!r}")
        try:
            body = TicketTemplateIn(**{k: v for k, v in t.items() if k not in {"queue", "checklist"}}, queue_id=queue_id, checklist_id=checklist_id)
        except ValidationError as exc:
            raise configstore.SettingsError(f"ticket_templates: {name}: invalid template fields") from exc
        row = session.scalar(select(TicketTemplate).where(func.lower(TicketTemplate.name) == body.name.lower())) or TicketTemplate()
        for field, value in body.model_dump().items():
            setattr(row, field, value)
        session.add(row)
        done.append(f"ticket template: {name}")
    if rules:
        from app import permissions
        from app.models import Group

        converted = {}
        for key, r in rules.items():
            ids = []
            for name in r.get("groups") or []:
                gid = session.scalar(select(Group.id).where(func.lower(Group.name) == str(name).strip().lower()).order_by(Group.id).limit(1))
                if gid is None:
                    raise configstore.SettingsError(f"permissions: no group called {name!r} (make it under Settings > Groups, or sync the directory, first)")
                ids.append(gid)
            converted[key] = {"who": r.get("who"), "group_ids": ids + list(r.get("group_ids") or [])}
        try:
            permissions.save(session, converted)
        except permissions.PermissionError_ as exc:
            raise configstore.SettingsError(f"permissions: {exc}") from None
        done.append(f"permissions: {', '.join(sorted(converted))}")
    for page, layout in (answers.get("layouts") or {}).items():
        from app import layouts

        try:
            layouts.set_default(session, page, layout, None)
        except layouts.LayoutError as exc:
            raise configstore.SettingsError(f"layouts: {exc}") from None
        done.append(f"layout: {page}")
    if windows:
        config = kerberos.load(session)
        if windows.get("keytab_file"):
            config = kerberos.install_keytab(session, (base / windows["keytab_file"]).read_bytes(), None)
            done.append(f"windows_sign_in: keytab for {', '.join(config.keytab_principals)}")
        if "enabled" in windows or "silent" in windows:
            config.enabled = bool(windows.get("enabled", config.enabled))
            config.silent = bool(windows.get("silent", config.silent))
            if config.enabled and not config.keytab_principals:
                raise configstore.SettingsError("windows_sign_in: give a keytab_file to turn it on")
            kerberos.save(session, config, None)
            done.append(f"windows_sign_in: {'on' if config.enabled else 'off'}")
    return done


def setup(path: Path) -> None:
    """The installer's last step: one answers file for everything the app needs.

    {"install": {"admin": {"email", "name", "password"}, "demo": false},
     "general": {...}, "email": {...}, "directory": {...}, ... as for settings import}

    Settings are applied all or nothing; then the first admin (if given) and, for the offline
    demo, the sample data. Safe to run again with the same file."""
    answers = json.loads(path.read_text())
    install = answers.pop("install", {}) or {}
    with SessionLocal() as session:
        try:
            done = import_settings(session, answers, path.resolve().parent)
        except (configstore.SettingsError, kerberos.KeytabError, OSError) as exc:
            session.rollback()
            raise SystemExit(f"Nothing saved: {exc}") from None
        session.commit()
    print("\n".join(done) or "No settings in the answers file")
    if admin := install.get("admin"):
        create_admin(admin["email"], admin.get("name") or admin["email"], admin["password"])
    if install.get("demo"):
        from app import demo

        with SessionLocal() as session:
            done = demo.install(session)
            session.commit()
        print("Offline demo: " + "; ".join(done))


def check(what: str) -> None:
    """Test a connection with the stored settings (as Settings' test buttons do). Exits 1 on failure."""
    with SessionLocal() as session:
        configstore.refresh(session, force=True)
    from app.config import settings

    try:
        if what == "email":
            from app.mail import graph

            if not settings.mailbox:
                raise SystemExit("Email: no mailbox set")
            if graph.missing(settings):
                raise SystemExit("Email: still needed: " + ", ".join(graph.missing(settings)))
            r = graph.from_settings(settings).check()
            print(f"Email: connected to {r['mailbox']}: {r['inbox_items']} message(s) in the Inbox, {r['unread']} unread")
        elif what == "directory":
            from app.directory.sync import get_directory

            d = get_directory()
            if d is None:
                raise SystemExit("Directory: not set up")
            r = d.describe()
            print(f"Directory: connected to {r.get('server')} as {r.get('bound_as')} (search base {r.get('base_dn')})")
    except SystemExit:
        raise
    except Exception as exc:  # network, TLS, MSAL and LDAP errors: show them, don't crash
        raise SystemExit(f"{what.capitalize()}: {str(exc)[:500]}") from None


def show_settings() -> None:
    with SessionLocal() as session:
        configstore.refresh(session, force=True)
        for section in configstore.SECTIONS:
            print(f"[{section}]")
            for f in configstore.describe(section):
                value = ("set" if f["is_set"] else "not set") if f["kind"] == "secret" else json.dumps(f["value"])
                print(f"  {f['name']} = {value}{'  (from the environment)' if f['locked'] else ''}")
        config = kerberos.load(session)
        print("[windows_sign_in]")
        print(f"  enabled = {json.dumps(config.enabled)}, silent = {json.dumps(config.silent)}, keytab = {', '.join(config.keytab_principals) or 'none'}")


def _backup_passphrase(passphrase_file: str | None) -> str:
    """From a file (installer), else Settings > Backup's, else asked for."""
    from app.config import settings

    if passphrase_file:
        return Path(passphrase_file).read_text().strip()
    if settings.backup_passphrase:
        return settings.backup_passphrase
    if not sys.stdin.isatty():
        raise SystemExit("No backup passphrase: pass --passphrase-file")
    return getpass.getpass("Backup passphrase: ")


def _worker_running(session) -> bool:
    from app import sla
    from app.models import ServiceHeartbeat

    beat = session.get(ServiceHeartbeat, "worker")
    return beat is not None and (sla.utcnow() - beat.last_seen).total_seconds() < 60


def backup_command(args) -> None:
    from app.backup import archive
    from app.config import settings

    # Separate CLI processes must load the same stored secrets/settings as the API.
    with SessionLocal() as session:
        configstore.refresh(session, force=True)
    try:
        if args.action == "create":
            folder = Path(args.to) if args.to else archive.target_dir()
            with SessionLocal() as session:
                made = archive.create(session, folder=folder, passphrase=_backup_passphrase(args.passphrase_file))
            print(f"Wrote and verified {made.path} ({made.size / 1024 / 1024:.1f} MB)")
            if not args.to:
                for name in archive.prune(folder, settings.backup_keep):
                    print(f"Removed old backup {name}")
        elif args.action == "list":
            for b in archive.list_backups(Path(args.to) if args.to else None):
                print(f"{b.name}  {b.size / 1024 / 1024:8.1f} MB  {b.created_at:%Y-%m-%d %H:%M}")
        elif args.action == "verify":
            m = archive.verify(Path(args.file), _backup_passphrase(args.passphrase_file))
            print(f"OK: backup of {m.data.get('host')} made {m.created_at}, schema {m.revision}, {len(m.data['files'])} files, {m.data.get('counts')}")
        elif args.action == "restore":
            restore_backup(Path(args.file), _backup_passphrase(args.passphrase_file), yes=args.yes, force=args.force)
    except archive.BackupError as exc:
        raise SystemExit(f"Backup {args.action} failed: {exc}") from None


def restore_backup(path: Path, passphrase: str, *, yes: bool, force: bool) -> None:
    """Replace everything here with the backup. Services must be stopped; the schema is upgraded after."""
    from alembic import command
    from alembic.config import Config

    from app.backup import archive
    from app.config import settings

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    known = _known_revisions()
    print(f"Checking {path} ...")
    manifest = archive.verify(path, passphrase)
    if manifest.revision not in known:
        raise SystemExit(f"The backup's schema ({manifest.revision}) is newer than this version of the app: update the app first")
    with SessionLocal() as session:
        if _worker_running(session) and not force:
            raise SystemExit("The worker is running: stop the services first (systemctl stop helpdesk-worker helpdesk-api), or pass --force")
    print(f"Backup of {manifest.data.get('host')} made {manifest.created_at}: {manifest.data.get('counts')}")
    print(f"This replaces the database at {make_safe_url(settings.database_url)}, {settings.storage_dir} and {settings.secrets_dir}.")
    if not yes:
        if not sys.stdin.isatty():
            raise SystemExit("Pass --yes to restore without being asked")
        if input("Type RESTORE to go ahead: ").strip() != "RESTORE":
            raise SystemExit("Nothing changed")
    _, done = archive.restore(path, passphrase)
    print("\n".join(done))
    # A backup from an older version: bring the schema up to date.
    command.upgrade(cfg, "head")
    print("Schema up to date. Start the services again.")


def _known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    return {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}


def export_command(args) -> None:
    from app.backup import transfer
    from app.backup.archive import BackupError

    out = Path(args.file)
    if out.suffix != transfer.SUFFIX:
        out = out.with_name(out.name + transfer.SUFFIX)
    if out.exists():
        raise SystemExit(f"{out} already exists")
    try:
        with SessionLocal() as session:
            m = transfer.export(session, out, _backup_passphrase(args.passphrase_file))
    except BackupError as exc:
        raise SystemExit(f"Export failed: {exc}") from None
    rows = sum(t["rows"] for t in m.data["tables"].values())
    print(f"Wrote and checked {out}: {len(m.data['tables'])} tables, {rows} rows, {sum(1 for f in m.data['files'] if f.startswith('storage/'))} files")


def import_command(args) -> None:
    from app.backup import transfer
    from app.backup.archive import BackupError

    passphrase = _backup_passphrase(args.passphrase_file)
    with SessionLocal() as session:
        if not args.dry_run and _worker_running(session) and not args.force:
            raise SystemExit("The worker is running: stop the services first (systemctl stop helpdesk-worker helpdesk-api), or pass --force")
        if not args.dry_run and not args.yes:
            if not sys.stdin.isatty():
                raise SystemExit("Pass --yes to import without being asked (or --dry-run to try it first)")
            if input("This replaces all users, queues, settings and data here. Type IMPORT to go ahead: ").strip() != "IMPORT":
                raise SystemExit("Nothing changed")
        try:
            result = transfer.import_(session, Path(args.file), passphrase, dry_run=args.dry_run, known_revisions=_known_revisions())
        except BackupError as exc:
            session.rollback()
            raise SystemExit(f"Import failed, nothing changed: {exc}") from None
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    for table, n in result.tables.items():
        if n:
            print(f"{table}: {n}")
    print(f"files: {result.files}")
    print("\n".join(result.notes))
    print("Dry run: nothing was changed." if args.dry_run else "Imported. Enter the secrets again (Settings > Directory, Email, Windows sign-in, Backup), then start the services.")


def make_safe_url(url: str) -> str:
    from sqlalchemy import make_url

    return make_url(url).render_as_string(hide_password=True)


def _ask(question: str, default: str = "") -> str:
    answer = input(f"{question} " + (f"[{default}] " if default else "")).strip()
    return answer or default


def import_spiceworks_command(args) -> None:
    """python -m app.cli import-spiceworks: dry run (or import) a Spiceworks CSV export into one queue."""
    from app import spiceworks, trackit

    with SessionLocal() as session:
        try:
            report = spiceworks.run(session, args.file, queue_name=args.queue, apply=args.apply, departments=args.departments)
        except trackit.ImportError_ as exc:
            raise SystemExit(str(exc)) from None
    print("\n".join(report.lines()))


def import_trackit_command(args) -> None:
    """python -m app.cli import-trackit: show the fields found, (optionally) choose, dry run, import."""
    from app import trackit

    pairs = lambda items: dict(i.split("=", 1) for i in items if "=" in i)  # noqa: E731
    labels = {k.upper(): v for k, v in pairs(args.label).items()}
    skip = {s.upper() for s in args.skip}
    groups = pairs(args.group)
    departments = args.departments
    try:
        bundle = trackit.Bundle(args.file)
    except trackit.ImportError_ as exc:
        raise SystemExit(str(exc)) from None
    m = bundle.manifest
    print(f"Track-It! export of {m.get('database')} on {m.get('server')}, made {m.get('exported_at')}" + (f", tickets since {m['since']}" if m.get("since") else ""))
    found = trackit.detect(bundle)
    print("\nFields found on tickets (nothing is added to your forms or lists: imported details show on imported tickets only):")
    for f in found:
        share = f"{100 * f['used'] // max(f['total'], 1)}%"
        print(f"  {f['column']:<14} {f['name']:<18} on {f['used']} tickets ({share}), {f['distinct']} different values -> {f['becomes']}")
        if args.interactive:
            print("      commonest: " + "; ".join(f"{v} ({n})" for v, n in f["top"]))
    if args.interactive:
        print()
        for f in found:
            col = f["column"]
            if col == "OPENED_GROUP":
                for value, _ in f["top"]:
                    q = _ask(f"Track-It! group {value!r} goes to which queue? (blank: a queue with that name, else the default)", groups.get(value, ""))
                    if q:
                        groups[value] = q
            elif col == "DEPT":
                departments = _ask("Departments: match your existing choices only, add missing ones as choices, or skip?", departments)
            else:
                choice = _ask(f"{col} ({f['name']}): keep, rename to something, or skip? [keep/skip/new name]", "keep")
                if choice.lower() == "skip":
                    skip.add(col)
                elif choice.lower() != "keep":
                    labels[col] = choice
    with SessionLocal() as session:
        try:
            run = lambda apply: trackit.run(session, args.file, apply=apply, queue_name=args.queue, queue_map=groups, labels=labels, skip=skip, departments=departments)  # noqa: E731
            report = run(args.apply and not args.interactive)
            print()
            print("\n".join(report.lines()))
            if args.interactive and _ask("\nImport now? (yes/no)", "no").lower() in ("y", "yes"):
                print("\n".join(run(True).lines()))
        except trackit.ImportError_ as exc:
            raise SystemExit(str(exc)) from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="Create a user or promote an existing one to admin")
    p.add_argument("email")
    p.add_argument("name")
    p.add_argument("--password-stdin", action="store_true", help="Read the password from stdin (for installers)")
    p = sub.add_parser("set-password", help="Set or reset a local user's password")
    p.add_argument("email")
    p.add_argument("--password-stdin", action="store_true", help="Read the password from stdin (for installers)")
    p = sub.add_parser("two-factor-reset", help="Turn off someone's two-factor sign-in (a lost authenticator app)")
    p.add_argument("email")
    sub.add_parser("admins", help="List admins and whether they can sign in with a local password (break-glass)")
    sub.add_parser("sign-out-everyone", help="End every signed-in session (recovery: after a password reset, or a suspected break-in)")
    sub.add_parser("seed", help="Create a default queue and SLA policies")
    sub.add_parser("seed-demo", help="Demo queues, portal options and sample rules (for trying it out; not for real installs)")
    sub.add_parser("openapi", help="Print the API's OpenAPI schema (used to generate frontend types)")
    p = sub.add_parser("settings", help="Show settings, or import an installer answers file")
    p.add_argument("action", choices=["show", "import"])
    p.add_argument("file", nargs="?", help="import: answers JSON file ('-' for stdin)")
    p = sub.add_parser("check", help="Test the email (Microsoft Entra / Graph) or directory connection with the stored settings")
    p.add_argument("what", choices=["email", "directory"])
    p = sub.add_parser("setup", help="Installer: apply an answers file (settings, first admin, offline demo)")
    p.add_argument("file")
    p = sub.add_parser("export", help="Export configuration and data to move to another install (no secret files)")
    p.add_argument("file", help="Where to write it (.hdexport)")
    p.add_argument("--passphrase-file", help="Encrypt with the passphrase in this file (default: the backup passphrase, else asked)")
    p = sub.add_parser("import", help="Import an export into this new, empty install")
    p.add_argument("file")
    p.add_argument("--passphrase-file", help="The export's passphrase (default: the backup passphrase, else asked)")
    p.add_argument("--dry-run", action="store_true", help="Load it all, report, and change nothing")
    p.add_argument("--yes", action="store_true", help="Don't ask before replacing everything")
    p.add_argument("--force", action="store_true", help="Even though the worker seems to be running")
    p = sub.add_parser("backup", help="Back up, list, verify or restore (Settings > Backup sets the folder, time and passphrase)")
    p.add_argument("action", choices=["create", "list", "verify", "restore"])
    p.add_argument("file", nargs="?", help="verify/restore: the backup file")
    p.add_argument("--to", help="create/list: this folder instead of the backup folder")
    p.add_argument("--passphrase-file", help="Read the passphrase from this file (default: the one in Settings > Backup, else asked)")
    p.add_argument("--yes", action="store_true", help="restore: don't ask before replacing everything")
    p.add_argument("--force", action="store_true", help="restore: even though the worker seems to be running")
    p = sub.add_parser("import-trackit", help="Import a Track-It! export bundle (made with tools/trackit-export.ps1); a dry run unless --apply")
    p.add_argument("file", help="The bundle (.zip)")
    p.add_argument("--apply", action="store_true", help="Import for real (without it, nothing is saved)")
    p.add_argument("--queue", help="Put tickets in this queue (default: the default queue)")
    p.add_argument("--group", action="append", default=[], metavar="GROUP=QUEUE", help="Tickets Track-It!'s group GROUP opened go to queue QUEUE (repeatable; a group named like a queue goes there anyway)")
    p.add_argument("--label", action="append", default=[], metavar="FIELD=NAME", help="Call Track-It!'s field FIELD this on imported tickets (e.g. HD_LOOKUP01=Building; repeatable)")
    p.add_argument("--skip", action="append", default=[], metavar="FIELD", help="Leave Track-It!'s field FIELD out (e.g. HD_LOOKUP04; repeatable)")
    p.add_argument("--departments", choices=["match", "add", "skip"], default="match", help="match: only your existing department choices (default; others are kept as an imported detail); add: add missing ones as choices; skip")
    p.add_argument("--interactive", action="store_true", help="Go through the fields found, choose what to keep, see the dry run, then confirm")
    p = sub.add_parser("import-spiceworks", help="Import ticket history from a Spiceworks Cloud Help Desk CSV export into one queue; a dry run unless --apply")
    p.add_argument("file", help="The export (.csv)")
    p.add_argument("--queue", required=True, help="The queue the tickets go to, e.g. Maintenance (make it first)")
    p.add_argument("--apply", action="store_true", help="Import for real (without it, nothing is saved)")
    p.add_argument("--departments", choices=["match", "add", "skip"], default="match", help="match: only your existing department choices (default; others are kept as an imported detail); add: add missing ones; skip")
    args = parser.parse_args()

    if args.cmd == "import-trackit":
        import_trackit_command(args)
    elif args.cmd == "import-spiceworks":
        import_spiceworks_command(args)
    elif args.cmd == "create-admin":
        create_admin(args.email, args.name, read_password(args.password_stdin))
    elif args.cmd == "set-password":
        set_password(args.email, read_password(args.password_stdin))
    elif args.cmd == "two-factor-reset":
        reset_two_factor(args.email)
    elif args.cmd == "admins":
        list_admins()
    elif args.cmd == "sign-out-everyone":
        sign_out_everyone()
    elif args.cmd == "seed":
        seed()
    elif args.cmd == "seed-demo":
        seed_demo()
    elif args.cmd == "settings" and args.action == "show":
        show_settings()
    elif args.cmd == "settings":
        if not args.file:
            raise SystemExit("settings import needs an answers file")
        answers = json.load(sys.stdin if args.file == "-" else open(args.file))
        base = Path(".") if args.file == "-" else Path(args.file).resolve().parent
        with SessionLocal() as session:
            try:
                done = import_settings(session, answers, base)
            except (configstore.SettingsError, kerberos.KeytabError, OSError) as exc:
                session.rollback()
                raise SystemExit(f"Nothing saved: {exc}") from None
            session.commit()
        print("\n".join(done) or "Nothing to import")
    elif args.cmd == "check":
        check(args.what)
    elif args.cmd == "setup":
        setup(Path(args.file))
    elif args.cmd == "export":
        export_command(args)
    elif args.cmd == "import":
        import_command(args)
    elif args.cmd == "backup":
        if args.action in ("verify", "restore") and not args.file:
            raise SystemExit(f"backup {args.action} needs the backup file")
        backup_command(args)
    elif args.cmd == "openapi":
        from app.main import app

        print(json.dumps(app.openapi(), indent=1))


if __name__ == "__main__":
    main()
