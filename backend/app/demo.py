"""The offline demo: sample people, tickets, devices and scripts, so the suite can be tried
without AD, email or anything outside the box. Installed by the setup wizard's "Offline demo"
choice (python -m app.cli setup with "install": {"demo": true}); never on a real install.

Everyone signs in with local accounts; the sign-in page and a notice say it's a demo.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, configstore, scripts, services, sla
from app.models import (
    AuthSource,
    Device,
    DeviceSource,
    Impact,
    KbArticle,
    KbAudience,
    KbStatus,
    Priority,
    Queue,
    Script,
    ScriptKind,
    ScriptLanguage,
    ScriptVersion,
    ServiceNotice,
    TicketSource,
    TicketStatus,
    Urgency,
    User,
    UserRole,
)

DEMO_PASSWORD = "Demo-helpdesk-2026"
ADMIN = ("admin@demo.local", "Alex Admin", UserRole.admin, "IT")
PEOPLE = [
    ("jpark@demo.local", "Jordan Park", UserRole.agent, "IT"),
    ("dcole@demo.local", "Dana Cole", UserRole.agent, "IT"),
    ("ralvarez@demo.local", "Rosa Alvarez", UserRole.requester, "Finance"),
    ("mosei@demo.local", "Mike Osei", UserRole.requester, "Sales"),
    ("tnguyen@demo.local", "Tam Nguyen", UserRole.requester, "Human Resources"),
    ("bsmith@demo.local", "Ben Smith", UserRole.requester, "Warehouse"),
]
DEVICES = [
    ("WS-0142", "Windows 11 Enterprise", "ralvarez@demo.local", 3),
    ("WS-0143", "Windows 11 Enterprise", "mosei@demo.local", 1),
    ("LT-0207", "Windows 11 Enterprise", "tnguyen@demo.local", 0),
    ("WS-0031", "Windows 10 Enterprise", "bsmith@demo.local", 45),
    ("SW-CORE-1", "Cisco Meraki MS", None, None),
    ("PRN-2F-01", "HP LaserJet", None, None),
]
# subject, requester, queue, source, priority, status, assignee, age (hours), first reply, computer
TICKETS = [
    ("Can't connect to VPN from home", "mosei@demo.local", "Information Technology", TicketSource.portal, Priority.high, TicketStatus.open, "jpark@demo.local", 3, "Thanks Mike, can you try reconnecting after the update I just pushed?", "WS-0143"),
    ("Printer on 2nd floor jams every morning", "ralvarez@demo.local", "Information Technology", TicketSource.email, Priority.medium, TicketStatus.new, None, 20, None, None),
    ("New starter: laptop and accounts for Priya", "tnguyen@demo.local", "Human Resources", TicketSource.portal, Priority.medium, TicketStatus.open, "dcole@demo.local", 50, "Laptop is being imaged; accounts tomorrow.", None),
    ("Outlook keeps asking for my password", "ralvarez@demo.local", "Information Technology", TicketSource.email, Priority.medium, TicketStatus.waiting_on_user, "jpark@demo.local", 30, "Could you restart Outlook and tell me if it still asks?", "WS-0142"),
    ("Warehouse scanner PC very slow", "bsmith@demo.local", "Information Technology", TicketSource.portal, Priority.low, TicketStatus.open, "dcole@demo.local", 70, None, "WS-0031"),
    ("URGENT: core switch port flapping", "jpark@demo.local", "Information Technology", TicketSource.agent, Priority.critical, TicketStatus.open, "jpark@demo.local", 1, None, None),
    ("Access to the Sales shared drive", "mosei@demo.local", "Information Technology", TicketSource.portal, Priority.low, TicketStatus.resolved, "dcole@demo.local", 120, "Done: you're in the Sales-Share group now.", None),
    ("Office heating too low", "tnguyen@demo.local", "Maintenance", TicketSource.portal, Priority.medium, TicketStatus.new, None, 4, None, None),
]
# title, keywords, audience, body
ARTICLES = [
    (
        "Connect to the VPN from home", "vpn anyconnect remote access work from home", KbAudience.everyone,
        "## Before you start\n\nYou need your laptop and your usual sign-in.\n\n## Steps\n\n1. Connect to your home Wi-Fi.\n2. Open **AnyConnect** from the Start menu.\n3. Choose `vpn.demo.local` and sign in.\n\n> Still stuck? Restart the laptop and try once more, then raise a request.",
    ),
    (
        "Outlook keeps asking for a password", "mail prompt credentials login", KbAudience.everyone,
        "Usually an old saved password after a password change.\n\n## Fix\n\n1. Close Outlook.\n2. Open **Credential Manager** (Start, type *credential*).\n3. Under *Windows Credentials*, remove the entries that start with `MicrosoftOffice`.\n4. Open Outlook and sign in once.",
    ),
    (
        "Clear a paper jam on the 2nd floor printer", "printer jam paper PRN-2F-01", KbAudience.everyone,
        "1. Open the front door and pull out the toner.\n2. Gently pull the paper out the way it travels.\n3. Check tray 2 for folded sheets.\n\nIf it jams every day, raise a request: the rollers may need replacing.",
    ),
    (
        "Reset a switch port on the Meraki dashboard", "meraki port cycle bounce switch", KbAudience.staff,
        "## When\n\nA device on a wall port has no network and the cable is fine.\n\n## Steps\n\n1. Find the device's port under **Switching > Switch ports** (search by MAC).\n2. Choose **Cycle port**.\n3. Note it on the ticket.",
    ),
]


def _user(session: Session, email: str, name: str, role: UserRole, department: str) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name=name, role=role, auth_source=AuthSource.local, role_source="manual", department=department)
        session.add(user)
        session.flush()
    auth.set_password(session, user, DEMO_PASSWORD)
    return user


def install(session: Session) -> list[str]:
    """Load the demo (safe to run again). Returns what was done."""
    from app.cli import seed_demo  # queues, portal options and sample routing rules

    seed_demo(session)
    done = ["Demo departments, IT teams and routing rules"]
    admin = _user(session, *ADMIN)
    people = {email: _user(session, email, name, role, dept) for email, name, role, dept in PEOPLE}
    people[ADMIN[0]] = admin
    session.flush()
    done.append(f"{len(people)} people, all with the password {DEMO_PASSWORD}")

    now = sla.utcnow()
    devices = {}
    for name, os_name, owner, seen_days in DEVICES:
        d = session.scalar(select(Device).where(Device.name == name))
        if d is None:
            d = Device(name=name, source=DeviceSource.manual)
            session.add(d)
        d.dns_hostname, d.os = f"{name.lower()}.demo.local", os_name
        d.assigned_user_id = people[owner].id if owner else None
        d.last_logon_at = now - timedelta(days=seen_days) if seen_days is not None else None
        devices[name] = d
    session.flush()
    done.append(f"{len(devices)} devices")

    queues = {q.name: q for q in session.scalars(select(Queue))}
    made = 0
    for subject, requester, queue, source, priority, status, assignee, hours, reply, computer in TICKETS:
        if session.scalar(select(services.Ticket.id).where(services.Ticket.subject == subject)) is not None:
            continue
        t = services.create_ticket(
            session, subject=subject, description="(Demo ticket.)", category=None, requester=people[requester], source=source,
            priority=priority, actor=people[requester], requested_queue=queues.get(queue), urgency=Urgency.medium if priority != Priority.critical else Urgency.high,
            impact=Impact.single, computer=devices.get(computer) if computer else None,
        )
        t.priority = priority
        if assignee:
            services.assign(session, t, admin, assignee_id=people[assignee].id)
        if reply:
            services.add_comment(session, t, people[assignee] if assignee else admin, body=reply, is_internal=False, source=TicketSource.agent, email_requester=False)
        if status != t.status:
            t.status = status
            if status == TicketStatus.resolved:
                t.resolved_at = now
        t.created_at = now - timedelta(hours=hours)
        made += 1
    done.append(f"{made} tickets")

    for ex in scripts.EXAMPLES:
        if session.scalar(select(Script).where(Script.name == ex["name"])) is None:
            s = Script(name=ex["name"], description=ex["description"], kind=ScriptKind.query, language=ScriptLanguage.sql, agents_can_run=True, current_version=1)
            s.versions.append(ScriptVersion(number=1, body=ex["body"], parameters=scripts.check_definition(ScriptKind.query, ScriptLanguage.sql, ex["parameters"]), note="Example", author_id=admin.id))
            session.add(s)
    done.append("Example queries in the script library")

    for title, keywords, audience, body in ARTICLES:
        if session.scalar(select(KbArticle).where(KbArticle.title == title)) is None:
            session.add(KbArticle(title=title, keywords=keywords, audience=audience, body=body, status=KbStatus.published, published_at=now, author_id=admin.id, updated_by_id=admin.id))
    done.append(f"{len(ARTICLES)} knowledge base articles")

    configstore.update(
        session, "branding",
        {"product_name": "Layer 8 (demo)", "sign_in_message": f"Demo: sign in as {ADMIN[0]} (or jpark@demo.local, ralvarez@demo.local...) with {DEMO_PASSWORD}"},
        None,
    )
    if session.scalar(select(ServiceNotice).where(ServiceNotice.title == "This is a demo")) is None:
        session.add(ServiceNotice(title="This is a demo", message="Sample data only. Nothing here reaches email, AD or anyone outside this machine.", severity="info", audience="everyone", starts_at=now, created_by_id=admin.id))
    done.append("Demo branding and notice")
    return done
