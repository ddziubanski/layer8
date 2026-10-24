"""Action1 (patching, read-only): reads each machine's patch state over Action1's REST API and
matches it to a device by name. Patching itself stays in Action1; Layer 8 shows it on devices and
tickets, and can open a ticket when patching is overdue.

The API: POST {region}/oauth2/token with the API credentials gives a bearer token;
GET /endpoints/managed/{org}?fields=* lists machines 50 at a time with missing_updates {critical, other},
vulnerabilities {critical, other}, reboot_required, last_seen... (checked against the live API; the
docs' flat missing_critical_updates / missing_other_updates are read too). Written from Action1's
public API docs and responses; nothing copied from PSAction1.

Patch automations (GET /automations/schedules/{org}: deploy updates, update rings, reboots) become
pre-approved standard changes when their next run is near, so they show on the change calendar and
in overlap warnings; after the run, the automation instance's status is the change's outcome.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import changes, services, sla
from app.config import settings
from app.models import Change, ChangeKind, ChangeStatus, Device, DevicePatch, EventType, Priority, Ticket, TicketDevice, TicketSource, TicketStatus

log = logging.getLogger(__name__)

HOSTS = {
    "NorthAmerica": "https://app.action1.com/api/3.0",
    "NorthAmerica-2": "https://app.na-2.action1.com/api/3.0",
    "Europe": "https://app.eu.action1.com/api/3.0",
    "UnitedKingdom": "https://app.uk.action1.com/api/3.0",
    "Australia": "https://app.au.action1.com/api/3.0",
}
PAGE = 50
# What BIOSes report when they don't know.
JUNK = {"0", "none", "default string", "to be filled by o.e.m.", "system serial number", "system manufacturer", "undefined"}
MAX_PAGES = 400
OPEN = (TicketStatus.new, TicketStatus.open, TicketStatus.waiting_on_user)
# Automation actions that patch or reboot machines (others, such as running a script, aren't changes here).
PATCHING = {"deploy_update": "Deploy updates", "update_ring": "Update ring", "reboot": "Reboot"}
# An automation run that started this long before a change's planned start still counts as its run.
SLACK = timedelta(hours=1)


class Action1Error(RuntimeError):
    """The message is safe to show."""


def enabled() -> bool:
    """Credentials are saved (they can be tested)."""
    return bool(settings.action1_client_id and settings.action1_client_secret)


def on() -> bool:
    """Action1 is in use (Settings > General) and set up: it's read and shown."""
    return settings.action1_enabled and enabled()


def org_ids() -> list[str]:
    """The organizations chosen in Settings; empty means all the credentials can see."""
    return [o.strip() for o in settings.action1_org_ids if o.strip()]


class Client:
    def __init__(self, base: str, client_id: str, secret: str, http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.base = base.rstrip("/")
        self._id, self._secret = client_id, secret
        self._http = http or httpx.Client(timeout=60)
        self._sleep = sleep
        self._token: str | None = None
        self._expires = 0.0

    def _auth(self) -> str:
        if self._token and time.monotonic() < self._expires:
            return self._token
        try:
            r = self._http.post(f"{self.base}/oauth2/token", data={"client_id": self._id, "client_secret": self._secret})
        except httpx.HTTPError as exc:
            raise Action1Error(f"Can't reach Action1: {exc}") from None
        if r.status_code in (400, 401, 403):
            raise Action1Error("Action1 didn't accept the client ID and secret")
        if r.status_code >= 400:
            raise Action1Error(f"Action1 sign-in answered {r.status_code}")
        body = r.json()
        self._token = body["access_token"]
        self._expires = time.monotonic() + max(int(body.get("expires_in") or 300) - 30, 30)
        return self._token

    def get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(4):
            try:
                r = self._http.get(f"{self.base}{path}", params=params, headers={"Authorization": f"Bearer {self._auth()}"})
            except httpx.HTTPError as exc:
                raise Action1Error(f"Can't reach Action1: {exc}") from None
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if r.status_code == 429 and attempt < 3:
                # Too many requests: Action1 asks for fewer than 30 a minute.
                self._sleep(float(r.headers.get("Retry-After") or 20))
                continue
            if r.status_code == 404:
                raise Action1Error(f"Action1 doesn't know {path} (check the organization ID)")
            if r.status_code >= 400:
                raise Action1Error(f"Action1 answered {r.status_code} for {path}")
            return r.json()
        raise Action1Error("Action1 kept refusing (too many requests)")

    def pages(self, path: str, params: dict | None = None):
        params = {**(params or {}), "from": 0, "limit": PAGE}
        for _ in range(MAX_PAGES):
            page = self.get(path, params)
            items = page.get("items") or []
            yield from items
            if not page.get("next_page") or not items:
                return
            params["from"] += len(items)

    def organizations(self) -> list[dict]:
        return [{"id": o.get("id", ""), "name": o.get("name", "")} for o in self.pages("/organizations")]

    def endpoints(self, org_id: str):
        return self.pages(f"/endpoints/managed/{org_id}", {"fields": "*"})

    def schedules(self, org_id: str):
        return self.pages(f"/automations/schedules/{org_id}")

    def group_endpoints(self, org_id: str, group_id: str):
        return self.pages(f"/endpoints/groups/{org_id}/{group_id}/contents")

    def instance(self, org_id: str, instance_id: str) -> dict:
        return self.get(f"/automations/instances/{org_id}/{instance_id}")


def from_settings(http: httpx.Client | None = None) -> Client:
    if not enabled():
        raise Action1Error("Save the client ID and secret first (Settings > Action1)")
    base = HOSTS.get(settings.action1_region, HOSTS["NorthAmerica"])
    return Client(base, settings.action1_client_id, settings.action1_client_secret, http)


def _when(text) -> datetime | None:
    """Action1 writes times as 2023-12-09_01-44-11 (UTC)."""
    if not text or not isinstance(text, str):
        return None
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _count(value) -> int:
    try:
        return max(int(str(value).strip() or 0), 0) if value is not None else 0
    except ValueError:
        return 0


def _match(session: Session, name: str) -> Device | None:
    """By name (PC-0042), or host name (pc-0042.corp.example) against the device's name or DNS name."""
    n = name.strip().lower()
    if not n:
        return None
    short = n.split(".")[0]
    return session.scalar(
        select(Device)
        .where(Device.is_active, (func.lower(Device.name) == short) | (func.lower(Device.dns_hostname) == n) | (func.lower(Device.name) == n))
        .order_by(Device.id)
        .limit(1)
    )


def sync(session: Session, client: Client, now: datetime | None = None) -> dict:
    """Read every machine from Action1 and update device_patches; open tickets for overdue patching.
    Doesn't commit. Returns counts for the job summary."""
    now = now or sla.utcnow()
    seen: set[str] = set()
    claimed: set[int] = set()
    counts = {"machines": 0, "matched": 0, "critical": 0, "reboot": 0, "tickets": 0}
    names = {o["id"]: o["name"] for o in client.organizations()}
    # None chosen: every organization the credentials can see (new ones are picked up by themselves).
    chosen = org_ids() or list(names)
    found = [(o, e) for o in chosen for e in client.endpoints(o)]
    # The same name twice (a re-installed agent, or a machine in two organizations): the one Action1
    # saw last gets the device.
    found.sort(key=lambda oe: _when(oe[1].get("last_seen")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for org_id, e in found:
        org_name = names.get(org_id, org_id)
        a1_id = str(e.get("id") or "")
        if not a1_id:
            continue
        seen.add(a1_id)
        row = session.scalar(select(DevicePatch).where(DevicePatch.action1_id == a1_id))
        if row is None:
            row = DevicePatch(action1_id=a1_id)
            session.add(row)
        row.name = str(e.get("name") or "")[:255]
        row.status = str(e.get("status") or "")[:50]
        row.os = str(e.get("OS") or "")[:255]
        row.agent_version = str(e.get("agent_version") or "")[:50]
        row.user = str(e.get("user") or "")[:255]
        row.last_seen_at = _when(e.get("last_seen"))
        row.last_boot_at = _when(e.get("last_boot_time"))
        missing = e.get("missing_updates") if isinstance(e.get("missing_updates"), dict) else {}
        vulns = e.get("vulnerabilities") if isinstance(e.get("vulnerabilities"), dict) else {}
        row.missing_critical = _count(missing.get("critical", e.get("missing_critical_updates")))
        row.missing_other = _count(missing.get("other", e.get("missing_other_updates")))
        row.vulns_critical, row.vulns_other = _count(vulns.get("critical")), _count(vulns.get("other"))
        row.organization = org_name[:200]
        row.organization_id = org_id
        row.reboot_required = str(e.get("reboot_required") or "").strip().lower() in ("yes", "true", "1")
        row.update_status = str(e.get("update_status") or "")[:50]
        row.vulnerability_status = str(e.get("vulnerability_status") or "")[:50]
        row.critical_since = (row.critical_since or now) if row.missing_critical else None
        row.reboot_since = (row.reboot_since or now) if row.reboot_required else None
        row.in_action1, row.synced_at = True, now
        device = _match(session, row.name)
        if device is not None and device.id not in claimed:
            claimed.add(device.id)
            # Fill the device's lifecycle details Action1 knows, when nobody has yet.
            for field, value in (("manufacturer", e.get("manufacturer")), ("serial", e.get("serial"))):
                value = str(value or "").strip()
                if value and value.lower() not in JUNK and not getattr(device, field):
                    setattr(device, field, value[:200])
            if row.device_id != device.id:
                # One record per device: a re-installed agent comes back with a new Action1 id.
                for other in session.scalars(select(DevicePatch).where(DevicePatch.device_id == device.id, DevicePatch.action1_id != a1_id)):
                    other.device_id = None
                session.flush()
                row.device_id = device.id
        counts["machines"] += 1
        counts["matched"] += row.device_id is not None
        counts["critical"] += row.missing_critical > 0
        counts["reboot"] += row.reboot_required
        session.flush()
        counts["tickets"] += _ticket_if_overdue(session, row, now)
    for gone in session.scalars(select(DevicePatch).where(DevicePatch.in_action1, DevicePatch.action1_id.not_in(seen or {""}))):
        gone.in_action1 = False
    return counts


def _ticket_if_overdue(session: Session, row: DevicePatch, now: datetime) -> int:
    reasons = []
    if settings.action1_critical_days and row.critical_since and row.critical_since <= now - timedelta(days=settings.action1_critical_days):
        reasons.append(f"{row.missing_critical} critical update(s) missing since {row.critical_since.astimezone():%d %B %Y}")
    if settings.action1_reboot_days and row.reboot_since and row.reboot_since <= now - timedelta(days=settings.action1_reboot_days):
        reasons.append(f"a reboot pending since {row.reboot_since.astimezone():%d %B %Y}")
    if not reasons:
        return 0
    if row.ticket_id is not None:
        open_ticket = session.get(Ticket, row.ticket_id)
        if open_ticket is not None and open_ticket.status in OPEN:
            return 0
    bot = services.automation_user(session)
    device = session.get(Device, row.device_id) if row.device_id else None
    lines = [
        f"Action1 reports {row.name} has " + " and ".join(reasons) + ".",
        "",
        f"Missing: {row.missing_critical} critical, {row.missing_other} other. Reboot pending: {'yes' if row.reboot_required else 'no'}.",
        f"Last seen by Action1: {row.last_seen_at.astimezone():%d %B %Y %H:%M}" if row.last_seen_at else "Last seen by Action1: unknown",
        f"Agent status: {row.status or 'unknown'}; logged-on user: {row.user or 'unknown'}.",
        "",
        "Patching is done in Action1; this ticket is for following it up.",
    ]
    ticket = services.create_ticket(
        session, subject=f"Patching overdue: {row.name}", description="\n".join(lines), category=None, requester=bot,
        source=TicketSource.api, priority=Priority.medium, actor=bot,
    )
    if device is not None:
        session.add(TicketDevice(ticket_id=ticket.id, device_id=device.id, linked_by_id=bot.id))
        services._event(session, ticket, EventType.device_linked, bot, device_id=device.id, device=device.name)
    row.ticket_id = ticket.id
    log.info("Action1: opened ticket #%s for %s (%s)", ticket.id, row.name, "; ".join(reasons), extra={"source": "action1"})
    return 1


# --- Patch automations as changes


def _patching(schedule: dict) -> list[str]:
    """What the automation does, when it patches or reboots: ["Deploy updates (All)", ...]."""
    out = []
    for a in schedule.get("actions") or []:
        kind = PATCHING.get(str(a.get("template_id") or ""))
        if kind:
            summary = str((a.get("params") or {}).get("display_summary") or "").strip()
            out.append(kind + (f" ({summary})" if summary else ""))
    return out


def _devices(session: Session, client: Client, org_id: str, org_name: str, schedule: dict, groups: dict) -> tuple[list[Device], list[str]]:
    """The devices it runs on (matched through device_patches), and what it targets in words."""
    ids: set[str] = set()
    words = []
    for t in schedule.get("endpoints") or []:
        tid, ttype = str(t.get("id") or ""), str(t.get("type") or "")
        if ttype == "EndpointGroup" and tid == "all":
            words.append(f"every machine in {org_name}")
            rows = session.scalars(select(DevicePatch).where(DevicePatch.organization == org_name[:200], DevicePatch.in_action1))
            ids |= {r.action1_id for r in rows}
        elif ttype == "EndpointGroup":
            if (org_id, tid) not in groups:
                try:
                    groups[(org_id, tid)] = {str(e.get("id") or "") for e in client.group_endpoints(org_id, tid)}
                except Action1Error as exc:
                    log.warning("Action1: can't list group %s: %s", tid, exc, extra={"source": "action1"})
                    groups[(org_id, tid)] = set()
            words.append(f"group {tid}")
            ids |= groups[(org_id, tid)]
        elif tid:
            ids.add(tid)
    picked = sum(1 for t in schedule.get("endpoints") or [] if t.get("type") == "Endpoint")
    if picked:
        words.append(f"{picked} machine(s) chosen one by one")
    ids.discard("")
    devices = list(session.scalars(
        select(Device).join(DevicePatch, DevicePatch.device_id == Device.id).where(DevicePatch.action1_id.in_(ids or {""})).order_by(Device.name)
    ))
    return devices, words


def _plans(schedule: dict, org_name: str, does: list[str], targets: list[str]) -> tuple[str, str, str]:
    tz = "each machine's local time" if schedule.get("settings_timezone") == "LOCALTIME" else "UTC"
    description = (
        f"Action1 automation \"{schedule.get('name', '')}\" in {org_name}: " + "; ".join(does) + ".\n"
        f"Runs on: {', '.join(targets) or 'no machines'}.\n"
        f"Schedule in Action1: {schedule.get('settings', '')} ({tz})."
    )
    implementation = "Action1 runs the automation on schedule; nothing to do here. Watch the run in Action1 > Automations."
    rollback = "Stop the run in Action1 (Automations > the running instance > Stop), then uninstall the update on affected machines from Action1 (Endpoints > the machine > Installed updates) or exclude it from the automation."
    return description, implementation, rollback


def _outcome(instance: dict) -> tuple[str, str]:
    """The change action for a finished run, and what to say: ('complete'|'fail'|'cancel'|'', note)."""
    status = str(instance.get("status") or "")
    done = str(instance.get("percent_completed") or "").strip()
    note = f"Action1: {status}" + (f", {done}% done" if done else "")
    return {"Success": "complete", "Warning": "complete", "Error": "fail", "Stopped": "cancel"}.get(status, ""), note


def sync_changes(session: Session, client: Client, now: datetime | None = None) -> dict:
    """Keep one change per upcoming run of each patch automation, and record how each run went.
    Doesn't commit. Returns counts for the job summary."""
    now = now or sla.utcnow()
    counts = {"made": 0, "moved": 0, "finished": 0, "cancelled": 0, "waiting": 0}
    if not settings.action1_change_days:
        return counts
    bot = services.automation_user(session)
    names = {o["id"]: o["name"] for o in client.organizations()}
    groups: dict = {}
    live: set[str] = set()
    for org_id in org_ids() or list(names):
        org_name = names.get(org_id, org_id)
        for sch in client.schedules(org_id):
            does = _patching(sch)
            sid = str(sch.get("id") or "")
            if not does or not sid:
                continue
            source = f"action1:{sid}"
            enabled = str(sch.get("settings") or "").upper().startswith("ENABLED")
            if enabled:
                live.add(source)
            c = session.scalar(select(Change).where(Change.source == source).order_by(Change.id.desc()).limit(1))
            if c is not None and c.status in changes.OPEN:
                counts[_follow(session, client, org_id, c, sch, enabled, now, bot)] += 1
                if c.status in changes.OPEN:
                    continue
            nxt = _when(sch.get("next_run"))
            if not enabled or nxt is None or nxt < now or nxt > now + timedelta(days=settings.action1_change_days):
                continue
            if c is not None and c.planned_start == nxt:
                continue  # this run's change was already made (and ended, or someone cancelled it)
            devices, targets = _devices(session, client, org_id, org_name, sch, groups)
            description, implementation, rollback = _plans(sch, org_name, does, targets)
            c = Change(
                title=f"Patching: {str(sch.get('name') or 'Action1 automation')[:250]}", kind=ChangeKind.standard, status=ChangeStatus.draft,
                description=description, implementation_plan=implementation, rollback_plan=rollback, impact=2 if len(devices) > 20 else 1,
                planned_start=nxt, planned_end=nxt + timedelta(hours=settings.action1_change_hours), requested_by_id=bot.id, source=source,
            )
            c.devices = devices
            session.add(c)
            changes.event(c, bot, "created", f"From the Action1 automation \"{sch.get('name', '')}\"")
            changes.act(session, c, bot, "submit")
            session.flush()
            counts["made"] += 1
            log.info("Action1: change %s for %r at %s", changes.label(c), sch.get("name"), nxt, extra={"source": "action1"})
    # Automations deleted or disabled in Action1: their upcoming changes go.
    for c in session.scalars(select(Change).where(Change.source.like("action1:%"), Change.status == ChangeStatus.approved)):
        if c.source not in live and c.planned_start and c.planned_start > now:
            changes.act(session, c, bot, "cancel", "The automation was disabled or deleted in Action1")
            counts["cancelled"] += 1
    return counts


def _follow(session: Session, client: Client, org_id: str, c: Change, sch: dict, enabled: bool, now: datetime, bot) -> str:
    """Move a change whose run Action1 rescheduled; start and end it with the run. Returns a counts key."""
    start = c.planned_start or now
    if c.status == ChangeStatus.approved and start > now:
        nxt = _when(sch.get("next_run"))
        if not enabled:
            changes.act(session, c, bot, "cancel", "The automation was disabled in Action1")
            return "cancelled"
        if nxt is not None and nxt != start:
            length = (c.planned_end - start) if c.planned_end else timedelta(hours=settings.action1_change_hours)
            changes.event(c, bot, "edited", f"Action1 moved the run to {nxt.astimezone():%d %B %Y %H:%M}")
            c.planned_start, c.planned_end = nxt, nxt + length
            return "moved"
        return "waiting"
    last = _when(sch.get("last_run"))
    instance_id = str(sch.get("last_instance") or "").rstrip("/").rsplit("/", 1)[-1]
    if last is None or last < start - SLACK or not instance_id:
        if now > (c.planned_end or start) + timedelta(days=1) and c.status == ChangeStatus.approved:
            changes.act(session, c, bot, "cancel", "Action1 didn't run the automation in its window")
            return "cancelled"
        return "waiting"
    instance = client.instance(org_id, instance_id)
    action, note = _outcome(instance)
    if c.status == ChangeStatus.approved:
        changes.act(session, c, bot, "start", "Action1 started the run")
    if action in ("complete", "fail"):
        changes.act(session, c, bot, action, note)
        return "finished"
    if action == "cancel":
        changes.act(session, c, bot, "cancel", note)
        return "cancelled"
    return "waiting"
