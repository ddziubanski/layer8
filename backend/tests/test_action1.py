"""Action1 (patching, read-only): reading machines' patch state, matching devices, overdue tickets."""

from datetime import timedelta

import httpx
import pytest

from app import action1, sla
from app.config import settings
from app.models import Device, DevicePatch, Queue, Ticket, TicketDevice, TicketStatus
from tests.conftest import as_user

BASE = "https://app.action1.com/api/3.0"
ORG = "88c8b425-871e-4ff6-9afc-00df8592c6db"
SERVERS = "31878170-6610-11f1-8598-a742d03f11a2"


def machine(id_, name, critical=0, other=0, reboot="No", vulns=(0, 0), **over):
    """As the live API sends it (checked 2026-10-20): counts nested in missing_updates and vulnerabilities."""
    return {
        "id": id_, "type": "Endpoint", "name": name, "status": "Connected", "last_seen": "2026-10-20_08-15-00", "OS": "Windows 11 (24H2)",
        "agent_version": "5.300.1", "user": f"CORP\\{name.lower()}", "reboot_required": reboot, "last_boot_time": "2026-10-13_07-00-00",
        "missing_updates": {"critical": critical, "other": other}, "vulnerabilities": {"critical": vulns[0], "other": vulns[1]},
        "update_status": "WARNING", "vulnerability_status": "UNDEFINED", **over,
    }


class FakeAction1:
    """Enough of Action1's API: the token, organizations, and managed endpoints 50 to a page."""

    def __init__(self, machines):
        self.machines = machines
        self.servers: list[dict] = []
        self.calls: list[str] = []
        self.secret = "s3cret"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/oauth2/token"):
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            if body.get("client_secret") != self.secret:
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/organizations"):
            return httpx.Response(200, json={"items": [{"id": ORG, "name": "Example Org"}, {"id": SERVERS, "name": "EXAMPLE - SERVERS ONLY"}], "next_page": ""})
        if request.url.path == f"/api/3.0/endpoints/managed/{SERVERS}":
            return httpx.Response(200, json={"items": self.servers, "next_page": ""})
        if request.url.path == f"/api/3.0/endpoints/managed/{ORG}":
            assert request.url.params["fields"] == "*"
            start, limit = int(request.url.params["from"]), int(request.url.params["limit"])
            page = self.machines[start : start + limit]
            more = start + limit < len(self.machines)
            return httpx.Response(200, json={"items": page, "total_items": str(len(self.machines)), "next_page": "next" if more else ""})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    f = FakeAction1([])
    monkeypatch.setattr(settings, "action1_client_id", "client")
    monkeypatch.setattr(settings, "action1_client_secret", "s3cret")
    monkeypatch.setattr(settings, "action1_enabled", True)
    # Only this one (the fake also has a servers organization).
    monkeypatch.setattr(settings, "action1_org_ids", [ORG])
    http = httpx.Client(transport=httpx.MockTransport(f.handler))
    real = action1.from_settings
    monkeypatch.setattr(action1, "from_settings", lambda http_=None: real(http))
    return f


def test_sync_matches_devices_and_pages(db, fake):
    db.add_all([Device(name="WS-0142", dns_hostname="ws-0142.corp.example"), Device(name="LT-0207")])
    db.commit()
    fake.machines = [machine("a1", "WS-0142", critical=3, other=5, reboot="Yes"), machine("a2", "lt-0207.corp.example")]
    fake.machines += [machine(f"x{i}", f"KIOSK-{i:02d}") for i in range(60)]  # two pages
    c = action1.sync(db, action1.from_settings())
    db.commit()
    assert c == {"machines": 62, "matched": 2, "critical": 1, "reboot": 1, "tickets": 0}
    assert fake.calls.count(f"GET /api/3.0/endpoints/managed/{ORG}") == 2 and fake.calls.count("POST /api/3.0/oauth2/token") == 1
    ws = db.query(DevicePatch).filter_by(action1_id="a1").one()
    assert (ws.device.name, ws.missing_critical, ws.missing_other, ws.reboot_required) == ("WS-0142", 3, 5, True)
    assert ws.last_seen_at.isoformat() == "2026-10-20T08:15:00+00:00" and ws.critical_since and ws.reboot_since
    assert db.query(DevicePatch).filter_by(action1_id="a2").one().device.name == "LT-0207"

    # Next sync: patched and rebooted, and one machine gone from Action1.
    first_seen = ws.critical_since
    fake.machines = [machine("a1", "WS-0142"), *fake.machines[1:-1]]
    action1.sync(db, action1.from_settings())
    db.commit()
    db.refresh(ws)
    assert ws.missing_critical == 0 and ws.critical_since is None and ws.reboot_since is None and first_seen
    assert db.query(DevicePatch).filter_by(action1_id="x59").one().in_action1 is False


def test_wrong_secret_says_so(db, fake):
    fake.secret = "other"
    with pytest.raises(action1.Action1Error, match="didn't accept"):
        action1.sync(db, action1.from_settings())


def test_overdue_patching_opens_one_ticket(db, fake, monkeypatch):
    db.add(Queue(name="Desk", is_default=True))
    pc = Device(name="WS-0142")
    db.add(pc)
    db.commit()
    monkeypatch.setattr(settings, "action1_critical_days", 14)
    fake.machines = [machine("a1", "WS-0142", critical=2)]
    action1.sync(db, action1.from_settings())
    db.commit()
    assert db.query(Ticket).count() == 0  # missing, but not for 14 days yet
    row = db.query(DevicePatch).one()
    row.critical_since = sla.utcnow() - timedelta(days=15)
    db.commit()
    assert action1.sync(db, action1.from_settings())["tickets"] == 1
    db.commit()
    t = db.query(Ticket).one()
    assert t.subject == "Patching overdue: WS-0142" and "2 critical update(s) missing" in t.description and t.source == "api"
    assert db.get(TicketDevice, (t.id, pc.id)) is not None
    # Not again while that ticket is open; again once it's closed and it's still overdue.
    assert action1.sync(db, action1.from_settings())["tickets"] == 0
    t.status = TicketStatus.closed
    db.commit()
    assert action1.sync(db, action1.from_settings())["tickets"] == 1


def test_api(client, db, agent, admin, fake):
    pc = Device(name="WS-0142")
    db.add(pc)
    db.commit()
    fake.machines = [machine("a1", "WS-0142", critical=1), machine("a2", "UNKNOWN-PC", reboot="Yes")]
    action1.sync(db, action1.from_settings())
    db.commit()
    got = client.get(f"/api/action1/devices/{pc.id}", headers=as_user(agent)).json()
    assert got["missing_critical"] == 1 and got["name"] == "WS-0142"
    other = Device(name="NOT-IN-A1")
    db.add(other)
    db.commit()
    assert client.get(f"/api/action1/devices/{other.id}", headers=as_user(agent)).json() is None
    assert [m["name"] for m in client.get("/api/action1/machines", params={"problem": "unmatched"}, headers=as_user(agent)).json()] == ["UNKNOWN-PC"]
    assert [m["name"] for m in client.get("/api/action1/machines", params={"problem": "critical"}, headers=as_user(agent)).json()] == ["WS-0142"]
    s = client.get("/api/action1/status", headers=as_user(agent)).json()
    assert (s["enabled"], s["machines"], s["matched"], s["critical"], s["reboot"]) == (True, 2, 1, 1, 1)
    # Only admins test the connection.
    assert client.post("/api/action1/test", headers=as_user(agent)).status_code == 403
    t = client.post("/api/action1/test", headers=as_user(admin)).json()
    assert t["ok"] and t["missing_orgs"] == [] and t["organizations"][0]["name"] == "Example Org"


def test_connection_test_needs_no_organization(client, admin, fake, monkeypatch):
    # First setup: Test connection is how the organization ID is found.
    monkeypatch.setattr(settings, "action1_org_ids", ["not-mine"])
    t = client.post("/api/action1/test", headers=as_user(admin)).json()
    assert t["ok"] and t["organizations"][1] == {"id": SERVERS, "name": "EXAMPLE - SERVERS ONLY"} and t["missing_orgs"] == ["not-mine"]
    monkeypatch.setattr(settings, "action1_org_ids", [])
    assert client.post("/api/action1/test", headers=as_user(admin)).json()["ok"]
    monkeypatch.setattr(settings, "action1_client_secret", "")
    assert "client ID and secret" in client.post("/api/action1/test", headers=as_user(admin)).json()["error"]


def test_several_organizations_and_the_documented_format(db, fake, monkeypatch):
    db.add_all([Device(name="WS-0142"), Device(name="SRV-FILE01")])
    db.commit()
    monkeypatch.setattr(settings, "action1_org_ids", [ORG, SERVERS])
    flat = machine("w1", "WS-0142")
    del flat["missing_updates"]
    # The docs show flat counts (as text); still read.
    flat.update(missing_critical_updates="2", missing_other_updates="1")
    fake.machines = [flat]
    fake.servers = [machine("s1", "SRV-FILE01", critical=1, vulns=(3, 40))]
    c = action1.sync(db, action1.from_settings())
    db.commit()
    assert c["machines"] == 2 and c["matched"] == 2 and c["critical"] == 2
    ws, srv = (db.query(DevicePatch).filter_by(action1_id=x).one() for x in ("w1", "s1"))
    assert (ws.missing_critical, ws.missing_other, ws.organization) == (2, 1, "Example Org")
    assert (srv.missing_critical, srv.vulns_critical, srv.vulns_other, srv.organization) == (1, 3, 40, "EXAMPLE - SERVERS ONLY")


def test_no_organizations_chosen_reads_them_all(db, fake, monkeypatch):
    monkeypatch.setattr(settings, "action1_org_ids", [])
    fake.machines = [machine("w1", "WS-0142")]
    fake.servers = [machine("s1", "SRV-FILE01")]
    assert action1.sync(db, action1.from_settings())["machines"] == 2
    assert {r.organization for r in db.query(DevicePatch)} == {"Example Org", "EXAMPLE - SERVERS ONLY"}


def test_same_name_twice_the_latest_gets_the_device(db, fake, monkeypatch):
    db.add(Device(name="WS-0142"))
    db.commit()
    monkeypatch.setattr(settings, "action1_org_ids", [])
    fake.machines = [machine("old-agent", "WS-0142", last_seen="2026-09-01_08-00-00")]
    fake.servers = [machine("new-agent", "WS-0142", last_seen="2026-10-20_08-00-00")]
    assert action1.sync(db, action1.from_settings())["matched"] == 1
    assert db.query(DevicePatch).filter_by(action1_id="new-agent").one().device_id is not None
    assert db.query(DevicePatch).filter_by(action1_id="old-agent").one().device_id is None


def test_action1_fills_manufacturer_and_serial_when_empty(db, fake):
    blank, kept = Device(name="WS-0142"), Device(name="WS-0143", serial="MINE")
    db.add_all([blank, kept])
    db.commit()
    fake.machines = [machine("a1", "WS-0142", manufacturer="Dell Inc.", serial="ABC123"), machine("a2", "WS-0143", manufacturer="To Be Filled By O.E.M.", serial="XYZ")]
    action1.sync(db, action1.from_settings())
    db.commit()
    db.refresh(blank)
    db.refresh(kept)
    assert (blank.manufacturer, blank.serial) == ("Dell Inc.", "ABC123")
    assert (kept.manufacturer, kept.serial) == ("", "MINE")


# --- Patch automations as changes


def schedule(id_, name, next_run, template="deploy_update", enabled=True, endpoints=None, **over):
    """As GET /automations/schedules/{org} sends it (from Action1's API docs)."""
    return {
        "id": id_, "type": "AutomationSchedule", "name": name, "settings": ("ENABLED" if enabled else "DISABLED") + " WEEKLY:Wed AT:16-00-00",
        "settings_timezone": "UTC", "retry_minutes": "1440", "next_run": next_run, "last_run": "", "last_instance": "",
        "endpoints": endpoints if endpoints is not None else [{"id": "all", "type": "EndpointGroup"}],
        "actions": [{"name": "Deploy Update", "template_id": template, "params": {"scope": "All", "display_summary": "All"}}], **over,
    }


@pytest.fixture
def automations(fake, monkeypatch):
    """The fake, also answering automation schedules, group contents and instances."""
    fake.schedules, fake.groups, fake.instances = [], {}, {}
    plain = fake.handler

    def handler(request):
        path = request.url.path
        if path == f"/api/3.0/automations/schedules/{ORG}":
            fake.calls.append(f"GET {path}")
            return httpx.Response(200, json={"items": fake.schedules, "next_page": ""})
        if path.startswith(f"/api/3.0/endpoints/groups/{ORG}/"):
            fake.calls.append(f"GET {path}")
            return httpx.Response(200, json={"items": [{"id": i} for i in fake.groups[path.split("/")[-2]]], "next_page": ""})
        if path.startswith(f"/api/3.0/automations/instances/{ORG}/"):
            fake.calls.append(f"GET {path}")
            return httpx.Response(200, json=fake.instances[path.rsplit("/", 1)[-1]])
        return plain(request)

    fake.handler = handler
    http = httpx.Client(transport=httpx.MockTransport(lambda r: fake.handler(r)))
    monkeypatch.setattr(action1, "from_settings", lambda http_=None: action1.Client(BASE, "client", "s3cret", http))
    monkeypatch.setattr(settings, "action1_change_days", 7)
    monkeypatch.setattr(settings, "action1_change_hours", 4)
    return fake


def test_patch_automations_become_changes_and_follow_the_run(db, automations):
    from datetime import datetime, timezone

    from app.models import Change, ChangeKind, ChangeStatus

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    db.add_all([Device(name="WS-0142"), Device(name="LT-0207"), Device(name="SRV-01")])
    db.commit()
    automations.machines = [machine("a1", "WS-0142"), machine("a2", "LT-0207"), machine("a3", "SRV-01")]
    client = action1.from_settings()
    action1.sync(db, client, now)
    automations.groups["laptops_1"] = ["a2"]
    automations.schedules = [
        schedule("s-all", "Workstations: all updates", "2026-10-21_16-00-00"),
        schedule("s-ring", "Laptops: ring 2", "2026-10-22_16-00-00", template="update_ring", endpoints=[{"id": "laptops_1", "type": "EndpointGroup"}]),
        schedule("s-one", "Reboot SRV-01", "2026-10-23_02-00-00", template="reboot", endpoints=[{"id": "a3", "type": "Endpoint"}]),
        schedule("s-far", "Next month", "2026-11-20_16-00-00"),
        schedule("s-off", "Disabled", "2026-10-21_16-00-00", enabled=False),
        schedule("s-script", "A script", "2026-10-21_16-00-00", template="run_script"),
    ]
    c = action1.sync_changes(db, client, now)
    db.commit()
    assert c["made"] == 3
    made = {ch.source: ch for ch in db.query(Change).all()}
    assert set(made) == {"action1:s-all", "action1:s-ring", "action1:s-one"}
    every = made["action1:s-all"]
    assert every.kind == ChangeKind.standard and every.status == ChangeStatus.approved and every.title == "Patching: Workstations: all updates"
    assert every.planned_start == datetime(2026, 10, 21, 16, tzinfo=timezone.utc) and every.planned_end - every.planned_start == timedelta(hours=4)
    assert [d.name for d in every.devices] == ["LT-0207", "SRV-01", "WS-0142"] and "every machine in Example Org" in every.description
    assert [d.name for d in made["action1:s-ring"].devices] == ["LT-0207"] and "Update ring (All)" in made["action1:s-ring"].description
    assert [d.name for d in made["action1:s-one"].devices] == ["SRV-01"]
    assert [e.type for e in every.events] == ["created", "submitted"]

    # A second sync makes nothing new; Action1 moving a run moves its change.
    automations.schedules[2]["next_run"] = "2026-10-24_02-00-00"
    c = action1.sync_changes(db, client, now)
    assert c["made"] == 0 and c["moved"] == 1
    assert made["action1:s-one"].planned_start == datetime(2026, 10, 24, 2, tzinfo=timezone.utc)

    # Disabling one in Action1 cancels its change.
    automations.schedules[1]["settings"] = "DISABLED WEEKLY:Wed AT:16-00-00"
    action1.sync_changes(db, client, now)
    assert made["action1:s-ring"].status == ChangeStatus.cancelled

    # After the run: the instance's status is the outcome, and the next run gets a new change.
    later = datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)
    automations.schedules[0].update(last_run="2026-10-21_16-00-05", last_instance="/API/automations/instances/inst-1", next_run="2026-10-28_16-00-00")
    automations.instances["inst-1"] = {"id": "inst-1", "status": "Running", "percent_completed": "40"}
    action1.sync_changes(db, client, later)
    assert every.status == ChangeStatus.in_progress
    automations.instances["inst-1"].update(status="Error", percent_completed="100")
    c = action1.sync_changes(db, client, later)
    db.commit()
    assert c["finished"] == 1 and every.status == ChangeStatus.failed and every.outcome == "Action1: Error, 100% done"
    nxt = db.query(Change).filter_by(source="action1:s-all").order_by(Change.id.desc()).first()
    assert nxt.id != every.id and nxt.planned_start == datetime(2026, 10, 28, 16, tzinfo=timezone.utc)

    # Someone cancels the new one by hand: the same run isn't made again.
    from app import changes as changes_mod

    changes_mod.act(db, nxt, nxt.requested_by, "cancel")
    db.commit()
    assert action1.sync_changes(db, client, later)["made"] == 0


def test_patch_changes_off_and_missed_runs(db, automations, monkeypatch):
    from datetime import datetime, timezone

    from app.models import Change, ChangeStatus

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    automations.schedules = [schedule("s-all", "All updates", "2026-10-21_16-00-00")]
    monkeypatch.setattr(settings, "action1_change_days", 0)
    assert action1.sync_changes(db, action1.from_settings(), now)["made"] == 0 and not any("schedules" in c for c in automations.calls)
    monkeypatch.setattr(settings, "action1_change_days", 7)
    action1.sync_changes(db, action1.from_settings(), now)
    ch = db.query(Change).one()
    # Action1 never ran it: a day after its window it's cancelled, saying so.
    automations.schedules[0]["next_run"] = "2026-10-28_16-00-00"
    action1.sync_changes(db, action1.from_settings(), datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc))
    assert ch.status == ChangeStatus.cancelled and ch.events[-1].note == "Action1 didn't run the automation in its window"


def test_off_hides_and_reads_nothing(client, db, agent, admin, fake, monkeypatch):
    d = Device(name="WS-0142")
    db.add(d)
    db.commit()
    fake.machines = [machine("a1", "WS-0142", critical=3)]
    action1.sync(db, action1.from_settings())
    db.commit()
    assert client.get(f"/api/action1/devices/{d.id}", headers=as_user(agent)).json()["missing_critical"] == 3
    assert "action1" in client.get("/api/auth/me", headers=as_user(agent)).json()["features"]
    monkeypatch.setattr(settings, "action1_enabled", False)
    assert client.get(f"/api/action1/devices/{d.id}", headers=as_user(agent)).json() is None
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["features"] == []
    assert not action1.on() and action1.enabled()
