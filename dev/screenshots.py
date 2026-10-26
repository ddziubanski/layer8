# SPDX-License-Identifier: AGPL-3.0-or-later
"""Development only: screenshots of each feature into screenshots/, from the running dev app.

    dev/serve.sh &                                   # the app on http://localhost:8000
    backend/.venv/bin/python dev/screenshots.py      # writes screenshots/*.png and screenshots/README.md

Drives headless Chromium over the DevTools protocol. It signs in by adding short-lived sessions
straight to the dev database (an admin and a requester), removed again at the end, so no
passwords are needed. Pages that need data use the first ticket and device in the database.
"""

import asyncio
import base64
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
import websockets  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.auth import COOKIE, _hash_token  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Device, Ticket, User, UserRole, UserSession  # noqa: E402

BASE = "http://127.0.0.1:8000"
OUT = ROOT / "screenshots"
# Also taken with dark mode on (Settings > Branding), into screenshots/dark/.
DARK = ["01-sign-in", "02-queue", "03-ticket", "05-device", "05f-kb", "05k-ipam-subnet", "05o-network-gear", "06-reports", "13-portal"]
PORT = 9333
# Also taken with each fixed dark theme (Settings > Branding > Appearance), into screenshots/themes/<name>/.
THEMES = {"ember": "Ember", "ultraviolet": "Ultraviolet"}
THEMED = ["01-sign-in", "02-queue", "03-ticket", "13-portal"]
WIDTH, HEIGHT = 1440, 900
# A wallboard made for the run (and removed after), so its link is known.
WALLBOARD_TOKEN = "l8w_screenshot-wallboard-token-for-dev-only"




def click(*texts: str) -> str:
    """Script run in the page before the shot, to show a feature in use: click the buttons (or labelled controls) whose text starts with each of these, in turn."""
    return """(async () => {{ for (const t of {texts}) {{
      const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(t))
        || [...document.querySelectorAll('[aria-label]')].find(e => e.getAttribute('aria-label').startsWith(t));
      if (b) b.click();
      await new Promise(r => setTimeout(r, 700));
    }} }})()""".format(texts=json.dumps(list(texts)))


def in_ticket(section: str, tool: str | None = None, then: str = "") -> str:
    """Open a ticket section tab (Conversation, Details, Tools, Work, History) and, under Tools, one
    tool (Devices, Directory...); then run another of these scripts, if given."""
    return """(async () => {{
      await new Promise(r => setTimeout(r, 600));
      [...document.querySelectorAll('[role=tab]')].find(b => b.textContent.trim() === {section})?.click();
      await new Promise(r => setTimeout(r, 600));
      const tool = {tool};
      if (tool) {{
        [...document.querySelectorAll('[aria-label="Ticket tools"] button')].find(b => b.textContent.trim().endsWith(tool))?.click();
        await new Promise(r => setTimeout(r, 900));
      }}
      {then}
    }})()""".format(section=json.dumps(section), tool=json.dumps(tool), then=f"await {then};" if then else "")


def fill_then_click(values: dict[str, str], *texts: str) -> str:
    """Like click(), after typing into the labelled inputs (React needs the native setter and an input event)."""
    return """(async () => {{
      for (const t of {first}) {{
        const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(t));
        if (b) b.click();
        await new Promise(r => setTimeout(r, 700));
      }}
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      for (const [label, value] of Object.entries({values})) {{
        const l = [...document.querySelectorAll('label')].find(l => l.textContent.trim().startsWith(label));
        const i = l && l.querySelector('input');
        if (i) {{ set.call(i, value); i.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
        await new Promise(r => setTimeout(r, 100));
      }}
      for (const t of {rest}) {{
        const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(t))
          || [...document.querySelectorAll('label')].find(l => l.textContent.trim() === t);
        if (b) b.click();
        await new Promise(r => setTimeout(r, 900));
      }}
    }})()""".format(first=json.dumps(list(texts[:1])), values=json.dumps(values), rest=json.dumps(list(texts[1:])))


def shots(ticket_id: int | None, device_id: int | None, ad_ticket: int | None = None, person_id: int | None = None) -> list[tuple]:
    """(file, who, path, caption[, script run before the shot]): who is admin, requester or none."""
    out = [
        ("01-sign-in", "none", "/", "Sign-in page with branding (product name, message)"),
        ("02-queue", "admin", "/", "Agent queue: my tickets / my groups / all, views, technicians' colour stripes and personal colour rules; only the list scrolls"),
        ("02b-queue-bulk", "admin", "/", "Agent queue: select tickets to move or assign them together", click("Select all")),
        ("02c-queue-search", "admin", "/?scope=all&q=switch", "Search every ticket's subject, description and notes, closed history included; a number finds that ticket"),
        ("02d-person-card", "admin", "/", "Click a requester's name for their contact card instead of opening the ticket", """(async () => {
          await new Promise(r => setTimeout(r, 600));
          document.querySelector('.person-link')?.click();
          await new Promise(r => setTimeout(r, 900));
        })()"""),
    ]
    if person_id:
        out.append(("03h-person", "admin", f"/people/{person_id}", "A person's page: contact details and manager from AD, direct reports, devices, AD groups and every ticket they've logged"))
    if ticket_id:
        out.append(("03-ticket", "admin", f"/tickets/{ticket_id}", "Ticket: conversation, attachments, assignment, requester, devices, status"))
    if ticket_id:
        out.append(("03f-ticket-vms", "admin", f"/tickets/{ticket_id}", "Ticket > Tools > Devices: a linked Nutanix VM with its state, and a restart that asks first; each power action is noted on the ticket",
                    in_ticket("Tools", "Devices", """(async () => {
          [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Restart')?.click();
          await new Promise(r => setTimeout(r, 400));
          document.querySelector('[data-ticket-panel="vms"]')?.scrollIntoView({ block: 'center' });
          await new Promise(r => setTimeout(r, 400));
        })()""")))
    if ad_ticket:
        out.append(("03e-ticket-checklist", "admin", f"/tickets/{ad_ticket}", "Ticket > Work: a New starter checklist started for John Smith: each step a child ticket, due from the start date, with progress", in_ticket("Work")))
    if ad_ticket:
        out.append(("03b-ticket-ad", "admin", f"/tickets/{ad_ticket}", "Ticket > Tools > Directory: the requester's AD account: groups, an onboarding template, add a group, move OU, department and title (checked before it runs)", in_ticket("Tools", "Directory", click("Change groups or OU"))))
    if ad_ticket:
        out.append((
            "03c-ticket-new-account", "admin", f"/tickets/{ad_ticket}",
            "Onboarding: a new AD account from the ticket, with phone, extension, distribution lists as tick boxes, checked against AD before it's created",
            in_ticket("Tools", "Directory", fill_then_click({"First name": "John", "Last name": "Smith", "Phone number": "+1 555 0100", "Extension": "4321", "Job title": "Network engineer"}, "New account", "All Staff", "Network Alerts", "Check")),
        ))
    out += [
        ("04-devices", "admin", "/devices", "Devices: AD computers from directory sync and devices added by hand"),
        ("04b-devices-stale", "admin", "/devices?view=stale", "Devices > Stale: no sign of life from AD, Action1 or network scans for 90 days"),
        ("04c-devices-unknown", "admin", "/devices?view=unknown", "Devices > Unknown on the network: seen by scans or in Action1, but not a device here"),
        ("04d-devices-vms", "admin", "/devices?view=vms", "Devices > Virtual machines: VMs read from Nutanix Prism Central, with host, size, addresses and the device each matches"),
    ]
    if device_id:
        out.append(("05-device", "admin", f"/devices/{device_id}", "A device: details, patching from Action1, IP addresses, notes and linked tickets"))
    out += [
        ("05b-scripts", "admin", "/scripts", "Scripts: the library; a query run with its parameters", click("Devices not seen", "Run")),
        ("05c-script-action", "admin", "/scripts", "Scripts: an action with a parameter, ready to run on devices, a device group or the runner", click("Ping a device")),
        ("05d-script-runs", "admin", "/scripts", "Scripts: all runs, with output from the runner", click("All runs", "Output")),
        ("05f-kb", "admin", "/kb/2", "Knowledge base: search, drafts and published articles; who can read each one"),
        ("05g-kb-edit", "admin", "/kb/2/edit", "Knowledge base: writing an article in simple Markdown, with a preview"),
        ("05e-script-schedules", "admin", "/scripts", "Scripts: a schedule pinging a device group twice a week, with its last run", click("Ping a device", "Schedules")),
        ("05e-vendors", "admin", "/vendors", "Vendors: account, support line, portal, hours, contract and contacts", click("Cisco Meraki")),
        ("05f-systems", "admin", "/vendors/systems", "Systems: what a vendor supports, who to call, its addresses and devices", click("Core switching")),
        ("05k-ipam-subnet", "admin", "/ipam", "IP addresses: a subnet's map of static, reserved, leased, do-not-use and free addresses, the DHCP pool and gateway", click("10.1.20.0/24")),
        ("05m-ipam-scan", "admin", "/ipam", "IP addresses: a scan by a runner on the LAN (ping, reverse DNS, ARP) and what it found to review: apply or dismiss, nothing changes by itself", click("172.18.0.0/24")),
        ("05n-ipam-dhcp-dns", "admin", "/ipam", "IP addresses: Windows DHCP leases and reservations and DNS records read for a subnet, with the differences to review", click("10.1.20.0/24")),
        ("05o-network-gear", "admin", "/ipam/gear", "IP addresses > Network gear: a switch found by SNMP discovery, its LLDP neighbours, and what's plugged into each port (MAC, VLAN, documented address and device)", click("sw-hq-idf1")),
        ("05l-ipam-find", "admin", "/ipam/addresses", "IP addresses: find an address by IP, host name, MAC in any format, device or system", """(async () => {
          const i = document.querySelector('input[aria-label="Find an address"]');
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, '10.1.20.');
          i.dispatchEvent(new Event('input', { bubbles: true }));
          await new Promise(r => setTimeout(r, 800));
        })()"""),
        ("05n-changes", "admin", "/changes?", "Changes: risk-scored, with a window and owner; standard (pre-approved), normal and emergency"),
        ("05n2-change-calendar", "admin", "/changes", "Changes > Calendar: a month of change windows, coloured by risk", """(async () => {
          [...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === 'Calendar').click();
          await new Promise(r => setTimeout(r, 900));
        })()"""),
        ("05o-change", "admin", "/changes/1", "A change awaiting approval: plans (rollback required), risk, window, linked ticket and device, approve or reject with a note"),
        ("09d-settings-changes", "admin", "/settings/changes", "Settings > Changes: the risk that needs approval, who approves, and templates (pre-approved ones make standard changes)"),
        ("05g-purchase-orders", "admin", "/purchase-orders?all", "Purchase orders: search, status, what's in, totals"),
        ("05h-purchase-order", "admin", "/purchase-orders/1", "A purchase order: lines with tax, approval and ordering, receiving, heads-up to receiving, files and history"),
        ("05h2-purchase-order-arrange", "admin", "/purchase-orders/1", "A purchase order, arranged: its panels in a column, across the top or bottom, or hidden; for yourself or everyone", click("Arrange this page")),
        ("05i-receiving-sheet", "admin", "/purchase-orders/1/receiving", "Receiving sheet: what's expected, a QR code, and the address, PO number and code written out"),
        ("05j-receive-public", "none", "/receive?po=PO-00001&code=XPAHSZ", "The receiving page: no sign-in, PO number and code, then what arrived"),
        ("06-reports", "admin", "/reports", "Reports: ready-made, saved and custom reports; PDF/CSV, email and schedules"),
        ("06b-settings-general", "admin", "/settings/general", "Settings > General: portal address, ticket numbering and label format (with a preview), limits"),
        ("07-settings-branding", "admin", "/settings/branding", "Settings > Branding: name, texts, colours with live preview and contrast check, images"),
        ("07c-settings-surveys", "admin", "/settings/surveys", "Settings > Surveys: the share of solved tickets surveyed, how often one person is asked, and the results"),
        ("07d-settings-permissions", "admin", "/settings/permissions", "Settings > Permissions: who may delete tickets, publish articles, run ticket rules..., technicians, admins, or admins and chosen groups"),
        ("07b-settings-runners", "admin", "/settings/runners", "Settings > Runners: machines that run action scripts, with check-in status"),
        ("07c-settings-action1", "admin", "/settings/action1", "Settings > Action1: patch status read from Action1 (read only), test connection, machines without a device, overdue-patching tickets"),
        ("08-settings-backup", "admin", "/settings/backup", "Settings > Backup: schedule, passphrase, backups with verify"),
        ("09b-settings-onboarding", "admin", "/settings/onboarding", "Settings > Onboarding templates: groups (with nesting warnings), OU, department and title", click("Network team")),
        ("09c-settings-checklists", "admin", "/settings/checklists", "Settings > Checklists: onboarding and offboarding steps, each due days from the start date or last day, with a queue or technician; a step can disable the AD account on its day", click("Leaver")),
        ("09-settings-directory", "admin", "/settings/directory", "Settings > Directory: AD connection, sync, role mappings, computers"),
        ("10-settings-status", "admin", "/settings/status", "Settings > Status: services and worker jobs"),
        ("11-settings-log", "admin", "/settings/log", "Settings > Log: the system log"),
        ("12-settings-rules", "admin", "/settings/rules", "Settings > Ticket rules (KACE-style, incl. SQL rules)"),
        ("12b-preferences", "admin", "/preferences", "My preferences: my colour in the queue and my own colour rules"),
        ("13-portal", "requester", "/", "Self-service portal: welcome text and the guided request form"),
        ("14-portal-tickets", "requester", "/tickets", "Portal: my tickets"),
        ("15-portal-help", "requester", "/help", "Portal: help articles, searchable"),
        ("15b-portal-article", "requester", "/help/2", "Portal: an article, and whether it helped"),
        ("16-survey", "requester", "/survey/screenshot-survey-token?r=4", "Satisfaction survey: one click in the email answers; a comment can follow (no sign-in needed)"),
        ("17-wallboard", "none", f"/wallboard#{WALLBOARD_TOKEN}", "Wallboard for a big screen: alerts, counts, the oldest unassigned tickets, SLA due soonest, queues and technicians (a secret link, no sign-in)"),
        ("09e-settings-wallboards", "admin", "/settings/wallboards", "Settings > Wallboards: boards for big screens, by queue, each with its own secret link"),
        ("09f-settings-desk-hours", "admin", "/settings/desk-hours", "Settings > Desk hours: opening hours, closed days, the after-hours message and technicians' Teams status on the portal"),
        ("09k-settings-canned", "admin", "/settings/canned-replies", "Settings > Canned replies: your own and shared replies, per queue, with placeholders filled in from the ticket", click("New canned reply")),
        ("09l-settings-requester-emails", "admin", "/settings/requester-emails", "Settings > Requester emails: the acknowledgement, resolved and assigned emails, with a preview as you type"),
        ("09m-settings-sign-in", "admin", "/settings/sign-in", "Settings > Sign-in: two-factor for local admins, and Sign in with Microsoft (Entra ID)"),
        ("12e-two-factor", "admin", "/preferences", "My preferences > Two-factor sign-in: scan the QR code with an authenticator app, then confirm a code", click("Turn on", "Set up two-factor sign-in")),
        ("09h-settings-nutanix", "admin", "/settings/nutanix", "Settings > Nutanix: Prism Central connection, power actions from tickets, critical VMs with outage tickets and a wallboard tile"),
        ("09i-settings-unifi", "admin", "/settings/unifi", "Settings > UniFi: cloud or local console, what's read, outage tickets and guest Wi-Fi vouchers"),
        ("09j-settings-juniper", "admin", "/settings/juniper", "Settings > Juniper: EX switches read over Junos REST through a runner"),
        ("09g-settings-meraki", "admin", "/settings/meraki", "Settings > Meraki: read-only discovery by API key or OAuth, diagnostics and outage alerts, each opt-in"),
        ("12c-workspace", "admin", "/preferences", "My preferences > My workspace: a minimal sidebar or chosen modules (hidden ones stay reachable with Ctrl+K)"),
    ]
    return out


def make_sessions(session) -> dict[str, str]:
    """Cookie tokens for an admin and a requester (one hour)."""
    admin = session.scalar(select(User).where(User.role == UserRole.admin, User.is_active).order_by(User.id))
    requester = session.scalar(select(User).where(User.role == UserRole.requester, User.is_active, User.email.not_like("%automation%")).order_by(User.id))
    tokens = {}
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    for who, user in (("admin", admin), ("requester", requester)):
        if user is None:
            continue
        token = secrets.token_urlsafe(32)
        session.add(UserSession(token_hash=_hash_token(token), user_id=user.id, expires_at=expires))
        tokens[who] = token
    session.commit()
    return tokens


class Tab:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0
        self.events: list[dict] = []

    async def call(self, method: str, **params) -> dict:
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)


async def capture(tokens: dict[str, str], plan, out: Path = OUT) -> list[tuple[str, str]]:
    chrome = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chromium-browser")
    if not chrome:
        raise SystemExit("Needs Chromium or Chrome")
    profile = tempfile.mkdtemp(prefix="helpdesk-shots-")
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}", f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    done = []
    try:
        for _ in range(50):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json").json()
                page = next(t for t in targets if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                await asyncio.sleep(0.2)
        else:
            raise SystemExit("Chromium didn't start")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.call("Page.enable")
            await tab.call("Network.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
            for name, who, path, caption, *before in plan:
                await tab.call("Network.clearBrowserCookies")
                if who != "none":
                    if who not in tokens:
                        continue
                    await tab.call("Network.setCookie", name=COOKIE, value=tokens[who], url=BASE, path="/", httpOnly=True)
                await tab.call("Page.navigate", url=BASE + path)
                # Let the page fetch its data and settle.
                await asyncio.sleep(2.5)
                if before:
                    await tab.call("Runtime.evaluate", expression=before[0], awaitPromise=True)
                    await asyncio.sleep(2)
                # The whole page, not just the window (up to a sensible length). The console scrolls
                # inside its main panel, so measure that too and make the window tall enough to show it
                # all, except on the queue (only its list scrolls: the window size is the point there).
                height = HEIGHT
                if not name.startswith("02"):
                    measured = await tab.call(
                        "Runtime.evaluate",
                        expression="Math.max(document.documentElement.scrollHeight, ...[...document.querySelectorAll('.main')].map(m => m.scrollHeight + m.getBoundingClientRect().top))",
                        returnByValue=True,
                    )
                    height = min(max(HEIGHT, int(measured["result"]["value"])), 3000)
                await tab.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=height, deviceScaleFactor=1, mobile=False)
                await asyncio.sleep(0.5)
                shot = await tab.call("Page.captureScreenshot", format="png")
                await tab.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
                (out / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
                done.append((name, caption))
                print(f"  {name}.png  {caption}")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)
    return done


def dark_mode(token: str, value: str | None = None) -> str:
    """Read Settings > Branding's dark mode, or set it (returns what it was)."""
    client = httpx.Client(base_url=BASE, cookies={COOKIE: token}, headers={"X-Helpdesk-CSRF": "1"}, timeout=10)
    was = next(f["value"] for f in client.get("/api/settings/theme").raise_for_status().json()["fields"] if f["name"] == "dark_mode")
    if value is not None:
        client.put("/api/settings/theme", json={"values": {"dark_mode": value}}).raise_for_status()
    return was


# Made-up VMs for the run (and removed after), so the Nutanix pages have something to show.
DEMO_VMS = [
    ("fileserver01", "on", "ahv-node-1", 4, 16384, "10.20.0.11", True),
    ("sql01", "on", "ahv-node-2", 8, 65536, "10.20.0.12", True),
    ("app01", "on", "ahv-node-1", 4, 8192, "10.20.0.13", False),
    ("print01", "off", "ahv-node-3", 2, 4096, "10.20.0.14", True),
    ("build-agent", "on", "ahv-node-3", 8, 32768, "10.20.0.15", True),
    ("old-test", "off", "ahv-node-2", 2, 4096, "10.20.0.16", False),
]


def nutanix_demo(session, token: str, ticket_id: int | None, device_id: int | None) -> dict:
    """Turn Nutanix on with a made-up Prism Central (nothing reads it: no worker runs during capture)
    and add the demo VMs; returns what to put back."""
    from app import sla
    from app.models import NutanixVm, TicketNutanixVm

    from app.models import AppSetting

    client = httpx.Client(base_url=BASE, cookies={COOKIE: token}, headers={"X-Helpdesk-CSRF": "1"}, timeout=10)
    # From the database, not the API: the server's copy of the settings can lag behind it.
    stored = session.get(AppSetting, "nutanix")
    was = dict(stored.value) if stored else {}
    client.put("/api/settings/nutanix", json={"values": {
        "nutanix_enabled": True, "nutanix_url": "https://prism.corp.example:9440", "nutanix_username": "layer8-read", "nutanix_password": "not-a-real-password",
        "nutanix_power_enabled": True, "nutanix_critical_vms": ["sql01", "print01"], "nutanix_outage_tickets": True, "nutanix_wallboard": True}}).raise_for_status()
    now = sla.utcnow()
    for i, (name, state, host, cpus, memory, ip, tools) in enumerate(DEMO_VMS):
        session.add(NutanixVm(ext_id=f"screenshot-{i}", name=name, power_state=state, cluster_name="HQ-cluster", host_name=host, vcpus=cpus, memory_mb=memory,
                              ips=[ip], macs=[f"50:6b:8d:00:00:{i:02x}"], guest_tools=tools, guest_tools_reachable=tools and state == "on",
                              device_id=device_id if name == "fileserver01" else None, observed_at=now))
    session.flush()
    if ticket_id:
        session.add(TicketNutanixVm(ticket_id=ticket_id, vm_id="screenshot-0"))
    from app import nutanix
    from app.models import NetworkInventory

    for name in ("sql01", "print01"):
        vm = session.scalar(select(NutanixVm).where(NutanixVm.ext_id.like("screenshot-%"), NutanixVm.name == name))
        session.add(NetworkInventory(provider=nutanix.PROVIDER, source_id=nutanix.CRITICAL_SOURCE, device_key=vm.ext_id, name=name, kind="VM",
                                     state="online" if vm.power_state == "on" else "offline", observed_at=now))
    session.commit()
    return was


def nutanix_undo(session, token: str, was: dict) -> None:
    from app.models import NetworkInventory, NutanixVm

    session.execute(delete(NetworkInventory).where(NetworkInventory.device_key.like("screenshot-%")))
    session.execute(delete(NutanixVm).where(NutanixVm.ext_id.like("screenshot-%")))
    session.commit()
    client = httpx.Client(base_url=BASE, cookies={COOKIE: token}, headers={"X-Helpdesk-CSRF": "1"}, timeout=10)
    demo = ["nutanix_enabled", "nutanix_url", "nutanix_username", "nutanix_password", "nutanix_power_enabled", "nutanix_critical_vms", "nutanix_outage_tickets", "nutanix_wallboard"]
    client.put("/api/settings/nutanix", json={"values": was, "clear": [n for n in demo if n not in was]}).raise_for_status()


def theme_preset(token: str, value: str | None = None) -> str:
    """Read Settings > Branding's appearance, or set it (returns what it was)."""
    client = httpx.Client(base_url=BASE, cookies={COOKIE: token}, headers={"X-Helpdesk-CSRF": "1"}, timeout=10)
    was = next(f["value"] for f in client.get("/api/settings/theme").raise_for_status().json()["fields"] if f["name"] == "theme_preset")
    if value is not None:
        client.put("/api/settings/theme", json={"values": {"theme_preset": value}}).raise_for_status()
    return was


def main() -> None:
    try:
        httpx.get(f"{BASE}/api/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        raise SystemExit(f"The dev app isn't running on {BASE} (dev/serve.sh)") from None
    OUT.mkdir(exist_ok=True)
    with SessionLocal() as session:
        ticket_id = session.scalar(select(Ticket.id).order_by(Ticket.id))
        device_id = session.scalar(select(Device.id).where(Device.is_active).order_by(Device.assigned_user_id.is_(None), Device.id))
        # A ticket from an AD account, for the AD account card.
        ad_ticket = session.scalar(select(Ticket.id).join(User, Ticket.requester_id == User.id).where(User.directory_dn.is_not(None), Ticket.parent_id.is_(None)).order_by(Ticket.id.desc()))
        person_id = session.scalar(select(Ticket.requester_id).where(Ticket.id == ad_ticket)) if ad_ticket else None
        tokens = make_sessions(session)
        from app.api.wallboards import _hash
        from app.models import Wallboard

        session.execute(delete(Wallboard).where(Wallboard.token_hash == _hash(WALLBOARD_TOKEN)))
        session.add(Wallboard(name="Service desk TV", token_hash=_hash(WALLBOARD_TOKEN), prefix=WALLBOARD_TOKEN[:8]))
        session.commit()
        nutanix_was = nutanix_demo(session, tokens["admin"], ticket_id, device_id)
    try:
        plan = shots(ticket_id, device_id, ad_ticket, person_id)
        done = asyncio.run(capture(tokens, plan))
        was = dark_mode(tokens["admin"], "on")
        try:
            (OUT / "dark").mkdir(exist_ok=True)
            dark = asyncio.run(capture(tokens, [p for p in plan if p[0] in DARK], OUT / "dark"))
        finally:
            dark_mode(tokens["admin"], was)
        themed = {}
        was = theme_preset(tokens["admin"])
        try:
            for preset in THEMES:
                theme_preset(tokens["admin"], preset)
                (OUT / "themes" / preset).mkdir(parents=True, exist_ok=True)
                themed[preset] = asyncio.run(capture(tokens, [p for p in plan if p[0] in THEMED], OUT / "themes" / preset))
        finally:
            theme_preset(tokens["admin"], was)
    finally:
        with SessionLocal() as session:
            nutanix_undo(session, tokens["admin"], nutanix_was)  # while the admin session still works
            session.execute(delete(UserSession).where(UserSession.token_hash.in_([_hash_token(t) for t in tokens.values()])))
            session.execute(delete(Wallboard).where(Wallboard.token_hash == _hash(WALLBOARD_TOKEN)))
            session.commit()
    lines = ["# Screenshots", "", f"From the dev app, {datetime.now():%Y-%m-%d}. Regenerate with `backend/.venv/bin/python dev/screenshots.py`.", ""]
    for name, caption in done:
        lines += [f"## {caption}", "", f"![{caption}]({name}.png)", ""]
    lines += ["# Dark mode", "", "Settings > Branding > Dark mode: off, always, or following each person's device setting.", ""]
    for name, caption in dark:
        lines += [f"## {caption} (dark)", "", f"![{caption}, dark](dark/{name}.png)", ""]
    lines += ["# Dark themes", "", "Settings > Branding > Appearance: fixed dark palettes, alongside Midnight ([midnight/](midnight/README.md)).", ""]
    for preset, shots_taken in themed.items():
        for name, caption in shots_taken:
            lines += [f"## {caption} ({THEMES[preset]})", "", f"![{caption}, {THEMES[preset]}](themes/{preset}/{name}.png)", ""]
    (OUT / "README.md").write_text("\n".join(lines))
    print(f"{len(done)} screenshots in {OUT}")


if __name__ == "__main__":
    main()
