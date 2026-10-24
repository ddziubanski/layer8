# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nutanix against a mocked Prism Central (v4 API): reads, device matching, ticket links, power actions."""
import json

import httpx
import pytest

from app import configstore, nutanix, services
from app.config import settings
from app.models import Comment, Device, IpAddress, NetworkObservation, NutanixAction, NutanixVm, Subnet, TicketDevice, TicketSource
from tests.conftest import as_user


def vm(i, *, name=None, state="ON", tools=None, ip=None, mac=None):
    nic = {"backingInfo": {"macAddress": mac or f"50:6B:8D:00:00:{i:02X}"},
           "networkInfo": {"ipv4Info": {"learnedIpAddresses": [{"value": ip or f"10.0.5.{i}"}]}}}
    return {"extId": f"vm-{i:04d}", "name": name or f"vm{i}", "powerState": state, "numSockets": 2, "numCoresPerSocket": 2,
            "memorySizeBytes": 8 * 1024**3, "cluster": {"extId": "c1"}, "host": {"extId": "h1"}, "nics": [nic],
            **({"guestTools": tools} if tools else {})}


class FakePrism:
    def __init__(self):
        self.vms = [vm(1, name="APP01", tools={"isInstalled": True, "isReachable": True}, ip="10.0.9.9"), vm(2, name="db01"), vm(3, name="old-test", state="OFF")]
        self.vms += [vm(10 + i) for i in range(120)]  # more than one page
        self.tasks = {}
        self.posts = []
        self.fail = False

    def __call__(self, request: httpx.Request):
        assert request.headers["Authorization"].startswith("Basic ")
        if self.fail:
            return httpx.Response(503)
        path, params = request.url.path, request.url.params
        page, limit = int(params.get("$page", 0)), int(params.get("$limit", 50))
        if path == "/api/clustermgmt/v4.0/config/clusters":
            return httpx.Response(200, json={"data": [{"extId": "c1", "name": "HQ-cluster"}], "metadata": {"totalAvailableResults": 1}})
        if path == "/api/clustermgmt/v4.0/config/hosts":
            return httpx.Response(200, json={"data": [{"extId": "h1", "hostName": "ahv-node-1"}], "metadata": {"totalAvailableResults": 1}})
        if path == "/api/vmm/v4.0/ahv/config/vms":
            return httpx.Response(200, json={"data": self.vms[page * limit:(page + 1) * limit], "metadata": {"totalAvailableResults": len(self.vms)}})
        if path.startswith("/api/vmm/v4.0/ahv/config/vms/") and "/$actions/" in path:
            ext_id, action = path.split("/")[-3], path.split("/")[-1]
            self.posts.append((ext_id, action, request.headers.get("If-Match"), request.headers.get("NTNX-Request-Id")))
            self.tasks[f"ZXJnb24=:task-{len(self.posts)}"] = "RUNNING"
            return httpx.Response(202, json={"data": {"extId": f"ZXJnb24=:task-{len(self.posts)}"}})
        if path.startswith("/api/vmm/v4.0/ahv/config/vms/"):
            ext_id = path.split("/")[-1]
            found = next((v for v in self.vms if v["extId"] == ext_id), None)
            return httpx.Response(200, json={"data": found}, headers={"ETag": f'W/"etag-{ext_id}"'}) if found else httpx.Response(404)
        if path.startswith("/api/prism/v4.0/config/tasks/"):
            task_id = path.split("/")[-1]
            return httpx.Response(200, json={"data": {"extId": task_id, "status": self.tasks[task_id], **({"errorMessages": [{"message": "VM is busy"}]} if self.tasks[task_id] == "FAILED" else {})}})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    f = FakePrism()
    monkeypatch.setattr(settings, "nutanix_enabled", True)
    monkeypatch.setattr(settings, "nutanix_url", "https://prism.corp.example:9440")
    monkeypatch.setattr(settings, "nutanix_username", "layer8")
    monkeypatch.setattr(settings, "nutanix_password", "secret")
    monkeypatch.setattr(nutanix, "from_settings", lambda: nutanix.Client(
        settings.nutanix_url, "layer8", "secret", http=httpx.Client(transport=httpx.MockTransport(f), auth=("layer8", "secret"))))
    return f


def test_read_pages_names_addresses_and_device_matches(db, fake):
    db.add_all([Device(name="app01"), Device(name="db01"), Device(name="DB01", dns_hostname="db01.corp.example")])
    db.commit()
    summary = nutanix.sync(db, nutanix.from_settings())
    db.commit()
    assert summary == "123 VMs (1 not running), 1 matched to devices"
    app = db.get(NutanixVm, "vm-0001")
    assert (app.name, app.power_state, app.cluster_name, app.host_name, app.vcpus, app.memory_mb) == ("APP01", "on", "HQ-cluster", "ahv-node-1", 4, 8192)
    assert (app.ips, app.macs, app.guest_tools_reachable) == (["10.0.9.9"], ["50:6b:8d:00:00:01"], True)
    assert app.device.name == "app01"
    assert db.get(NutanixVm, "vm-0002").device_id is None  # two devices called db01: no guess
    assert db.get(NutanixVm, "vm-0003").power_state == "off"
    obs = db.get(NetworkObservation, ("nutanix", "HQ-cluster", "50:6b:8d:00:00:01"))
    assert (obs.hostname, obs.address, obs.uplink_name, obs.connection) == ("APP01", "10.0.9.9", "ahv-node-1", "vm")
    # A VM that's gone is kept (links and history stay readable) but marked absent.
    fake.vms = [v for v in fake.vms if v["extId"] != "vm-0003"]
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    assert db.get(NutanixVm, "vm-0003").present is False


def test_failed_read_changes_nothing(db, fake):
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    fake.fail = True
    with pytest.raises(nutanix.NutanixError, match="HTTP 503"):
        nutanix.sync(db, nutanix.from_settings())
    db.rollback()
    assert db.query(NutanixVm).filter_by(present=True).count() == 123


def test_vms_show_on_ip_records_and_lists(client, db, agent, fake):
    nutanix.sync(db, nutanix.from_settings())
    db.add(Subnet(network="10.0.5.0/24", name="Servers"))
    db.add(IpAddress(address="10.0.5.2", mac="50:6b:8d:00:00:02", hostname="db01"))
    db.commit()
    seen = client.get("/api/ipam/addresses?q=10.0.5.2", headers=as_user(agent)).json()[0]["observations"]
    assert seen[0]["provider_label"] == "Nutanix" and seen[0]["hostname"] == "db01"
    rows = client.get("/api/nutanix/vms?q=10.0.9.9", headers=as_user(agent)).json()
    assert [r["name"] for r in rows] == ["APP01"]
    assert client.get("/api/nutanix/vms?state=off", headers=as_user(agent)).json()[0]["name"] == "old-test"
    settings.nutanix_enabled = False
    assert client.get("/api/nutanix/vms", headers=as_user(agent)).status_code == 404


def _ticket(db, requester, **kw):
    t = services.create_ticket(db, subject="App is slow", description="", category=None, requester=requester, source=TicketSource.portal, actor=requester, **kw)
    db.commit()
    return t


def test_link_and_power_actions_are_permitted_noted_and_followed(client, db, admin, agent, requester, fake, monkeypatch, service_desk):
    nutanix.sync(db, nutanix.from_settings())
    ticket = _ticket(db, requester)
    url = f"/api/nutanix/tickets/{ticket.id}/vms"
    assert client.post(url, json={"ext_id": "nope"}, headers=as_user(agent)).status_code == 422
    assert client.post(url, json={"ext_id": "vm-0001"}, headers=as_user(agent)).status_code == 204
    listed = client.get(url, headers=as_user(agent)).json()
    assert [v["name"] for v in listed["vms"]] == ["APP01"] and listed["vms"][0]["linked"] is True
    assert (listed["power_enabled"], listed["may_power"]) == (False, False)
    restart = {"action": "restart"}
    assert client.post(f"{url}/vm-0001/power", json=restart, headers=as_user(admin)).status_code == 409  # off in Settings
    monkeypatch.setattr(settings, "nutanix_power_enabled", True)
    assert client.post(f"{url}/vm-0001/power", json=restart, headers=as_user(agent)).status_code == 403  # admins by default
    assert client.post(f"{url}/vm-0002/power", json=restart, headers=as_user(admin)).status_code == 404  # not this ticket's
    done = client.post(f"{url}/vm-0001/power", json=restart, headers=as_user(admin))
    assert done.status_code == 200, done.text
    ext_id, action, if_match, request_id = fake.posts[0]
    assert (ext_id, action, if_match) == ("vm-0001", "guest-reboot", 'W/"etag-vm-0001"') and len(request_id) == 36
    assert client.post(f"{url}/vm-0001/power", json=restart, headers=as_user(admin)).status_code == 409  # one at a time
    # The worker follows the task: still running, then finished, noted on the ticket.
    nutanix.follow_tasks(db, nutanix.from_settings())
    assert db.query(NutanixAction).one().status == "running"
    fake.tasks["ZXJnb24=:task-1"] = "SUCCEEDED"
    nutanix.follow_tasks(db, nutanix.from_settings())
    db.commit()
    notes = [c.body for c in db.query(Comment).filter_by(ticket_id=ticket.id, is_internal=True).order_by(Comment.id)]
    assert notes[0].startswith("Nutanix: asked for a graceful restart (through Nutanix Guest Tools) of VM APP01")
    assert notes[-1] == "Nutanix: the graceful restart of VM APP01 finished."
    history = client.get(url, headers=as_user(admin)).json()["actions"]
    assert history[0]["status"] == "succeeded" and history[0]["requested_by"] == admin.display_name


def test_vms_of_linked_devices_power_rules_and_failures(client, db, admin, requester, fake, monkeypatch, service_desk):
    monkeypatch.setattr(settings, "nutanix_power_enabled", True)
    db.add(Device(name="old-test"))
    db.commit()
    nutanix.sync(db, nutanix.from_settings())
    ticket = _ticket(db, requester)
    db.add(TicketDevice(ticket_id=ticket.id, device_id=db.query(Device).filter_by(name="old-test").one().id))
    db.commit()
    url = f"/api/nutanix/tickets/{ticket.id}/vms"
    listed = client.get(url, headers=as_user(admin)).json()["vms"]
    assert [(v["name"], v["linked"]) for v in listed] == [("old-test", False)]
    assert client.post(f"{url}/vm-0003/power", json={"action": "power_cycle"}, headers=as_user(admin)).status_code == 409  # it's off
    assert client.post(f"{url}/vm-0003/power", json={"action": "power_on"}, headers=as_user(admin)).status_code == 200
    assert fake.posts[-1][1] == "power-on"
    fake.tasks["ZXJnb24=:task-1"] = "FAILED"
    nutanix.follow_tasks(db, nutanix.from_settings())
    db.commit()
    last = db.query(Comment).filter_by(ticket_id=ticket.id).order_by(Comment.id.desc()).first()
    assert last.body == "Nutanix: the power on of VM old-test failed: VM is busy"


def test_restart_without_guest_tools_uses_acpi(client, db, admin, requester, fake, monkeypatch, service_desk):
    monkeypatch.setattr(settings, "nutanix_power_enabled", True)
    nutanix.sync(db, nutanix.from_settings())
    ticket = _ticket(db, requester)
    url = f"/api/nutanix/tickets/{ticket.id}/vms"
    client.post(url, json={"ext_id": "vm-0002"}, headers=as_user(admin))
    assert client.post(f"{url}/vm-0002/power", json={"action": "restart"}, headers=as_user(admin)).status_code == 200
    assert fake.posts[-1][1] == "reboot"


def test_settings_checks(db, admin):
    for bad in ({"nutanix_url": "http://prism.corp.example:9440"}, {"nutanix_url": "https://prism.corp.example/api"}, {"nutanix_sync_minutes": 1},
                {"nutanix_critical_vms": ["x" * 256]}, {"nutanix_critical_vms": [f"vm{i}" for i in range(201)]}):
        with pytest.raises(configstore.SettingsError):
            configstore.update(db, "nutanix", bad, admin)
    configstore.update(db, "nutanix", {"nutanix_url": "https://prism.corp.example:9440"}, admin)


def test_parse_vm_ignores_garbage():
    assert nutanix.parse_vm({"name": "no id"}, {}, {}) is None
    parsed = nutanix.parse_vm({"extId": "x", "powerState": "<b>", "nics": [{"backingInfo": {"macAddress": "zz"}, "networkInfo": {"ipv4Config": {"ipAddress": {"value": "999.1.1.1"}}}}], "memorySizeBytes": "lots"}, {}, {})
    assert (parsed["name"], parsed["power_state"], parsed["macs"], parsed["ips"], parsed["memory_mb"]) == ("x", "unknown", [], [], 0)
    json.dumps({k: v for k, v in parsed.items()})


def test_critical_vm_down_opens_one_ticket_after_the_grace_period_and_notes_recovery(db, fake, monkeypatch, service_desk):
    from datetime import timedelta

    from app import sla
    from app.models import NetworkOutage, Ticket, TicketStatus

    monkeypatch.setattr(settings, "nutanix_critical_vms", ["app01", "DB01", "typo-vm"])
    monkeypatch.setattr(settings, "nutanix_outage_tickets", True)
    fake.vms[0]["powerState"] = "OFF"
    summary = nutanix.sync(db, nutanix.from_settings())
    db.commit()
    assert "critical VMs down: 1 of 2" in summary and "never seen in Nutanix: typo-vm" in summary
    assert db.query(Ticket).count() == 0  # within the grace period
    db.get(NetworkOutage, ("nutanix", "vms", "vm-0001")).down_since = sla.utcnow() - timedelta(minutes=11)
    db.commit()
    nutanix.sync(db, nutanix.from_settings())
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    tickets = db.query(Ticket).all()
    assert [t.subject for t in tickets] == ["Offline: Nutanix VM APP01"]
    # Gone from Prism Central counts as down too.
    fake.vms = [v for v in fake.vms if v["extId"] != "vm-0002"]
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    assert db.get(NetworkOutage, ("nutanix", "vms", "vm-0002")).is_down
    fake.vms[0]["powerState"] = "ON"
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    assert db.get(Ticket, tickets[0].id).status != TicketStatus.resolved
    assert any("APP01 is back online" in c.body for c in db.query(Comment).filter_by(ticket_id=tickets[0].id))


def test_wallboard_shows_critical_vms_only_when_chosen(client, db, admin, fake, monkeypatch):
    from tests.test_wallboards import make_board, show

    monkeypatch.setattr(settings, "nutanix_critical_vms", ["APP01"])
    fake.vms[0]["powerState"] = "OFF"
    nutanix.sync(db, nutanix.from_settings())
    db.commit()
    token = make_board(client, admin)["token"]
    data = show(client, token).json()
    assert "critical_vms_down" not in data["totals"] and not any("APP01" in a["text"] for a in data["alerts"])
    monkeypatch.setattr(settings, "nutanix_wallboard", True)
    data = show(client, token).json()
    assert data["totals"]["critical_vms_down"] == 1
    assert data["alerts"][0] == {"level": "critical", "text": "Critical VM APP01 is down"}
