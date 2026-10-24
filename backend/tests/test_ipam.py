# SPDX-License-Identifier: AGPL-3.0-or-later
"""IPAM: VLANs, subnets (usage, map, next free) and documented IP addresses."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import ipam, ipamscan, sla
from app.models import Device, IpAddress, IpScan, RunStatus
from tests.conftest import as_user


def test_normalize_mac():
    for text in ("AA-BB-CC-DD-EE-01", "aabb.ccdd.ee01", "aa:bb:cc:dd:ee:01", "AABBCCDDEE01"):
        assert ipam.normalize_mac(text) == "aa:bb:cc:dd:ee:01"
    assert ipam.normalize_mac("  ") == ""
    for bad in ("aa:bb:cc", "zz:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff:00"):
        with pytest.raises(ipam.IpamError):
            ipam.normalize_mac(bad)


def test_vlans(client, agent, admin):
    r = client.post("/api/ipam/vlans", json={"number": 20, "name": "Staff", "site": "HQ"}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    # Same number at another site is fine; twice at one site isn't.
    assert client.post("/api/ipam/vlans", json={"number": 20, "name": "Staff", "site": "Plant"}, headers=as_user(agent)).status_code == 201
    assert client.post("/api/ipam/vlans", json={"number": 20, "site": "hq"}, headers=as_user(agent)).status_code == 409
    assert client.post("/api/ipam/vlans", json={"number": 4095}, headers=as_user(agent)).status_code == 422
    vid = r.json()["id"]
    assert client.delete(f"/api/ipam/vlans/{vid}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/ipam/vlans/{vid}", headers=as_user(admin)).status_code == 204


def test_subnet_usage_map_and_next_free(client, agent, admin):
    vlan = client.post("/api/ipam/vlans", json={"number": 20, "name": "Staff"}, headers=as_user(agent)).json()
    body = {"network": "10.1.20.7/24", "name": "Staff", "vlan_id": vlan["id"], "gateway": "10.1.20.1", "dhcp_start": "10.1.20.100", "dhcp_end": "10.1.20.199"}
    r = client.post("/api/ipam/subnets", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    s = r.json()
    # Host bits are dropped.
    assert s["network"] == "10.1.20.0/24" and s["vlan"]["number"] == 20
    assert s["usage"] == {"hosts": 254, "static": 0, "reserved": 0, "dhcp": 0, "do_not_use": 0, "pool": 100, "free": 153}
    assert client.post("/api/ipam/subnets", json={**body, "network": "10.1.20.0/24"}, headers=as_user(agent)).status_code == 409
    for bad in ({"network": "10.1.20.0/33"}, {"gateway": "10.1.21.1"}, {"dhcp_end": None}, {"dhcp_start": "10.1.20.200"}):
        assert client.post("/api/ipam/subnets", json={**body, "network": "10.9.0.0/24", **bad} if "network" not in bad else {**body, **bad}, headers=as_user(agent)).status_code == 422, bad

    # Next free skips the gateway and documented addresses.
    assert client.get(f"/api/ipam/subnets/{s['id']}/next-free", headers=as_user(agent)).json() == {"address": "10.1.20.2"}
    for a, kind in (("10.1.20.2", "static"), ("10.1.20.3", "do_not_use"), ("10.1.20.150", "reserved")):
        assert client.post("/api/ipam/addresses", json={"address": a, "kind": kind, "hostname": f"h{a[-1]}"}, headers=as_user(agent)).status_code == 201
    assert client.get(f"/api/ipam/subnets/{s['id']}/next-free", headers=as_user(agent)).json() == {"address": "10.1.20.4"}

    d = client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()
    # The reservation sits inside the pool, so it doesn't count twice.
    assert d["usage"] == {"hosts": 254, "static": 1, "reserved": 1, "dhcp": 0, "do_not_use": 1, "pool": 100, "free": 151}
    states = {c["address"]: c["state"] for c in d["cells"]}
    assert len(d["cells"]) == 256
    assert (states["10.1.20.0"], states["10.1.20.1"], states["10.1.20.2"], states["10.1.20.3"], states["10.1.20.4"]) == ("network", "gateway", "static", "do_not_use", "free")
    assert (states["10.1.20.100"], states["10.1.20.150"], states["10.1.20.255"]) == ("pool", "reserved", "broadcast")
    assert [a["address"] for a in d["addresses"]] == ["10.1.20.2", "10.1.20.3", "10.1.20.150"]

    # Search by an address inside it, or the VLAN number.
    for q in ("10.1.20.77", "20", "staff"):
        assert [x["id"] for x in client.get("/api/ipam/subnets", params={"q": q}, headers=as_user(agent)).json()] == [s["id"]], q

    # A more specific subnet inside it: listed as a child, and its addresses say they're in it.
    child = client.post("/api/ipam/subnets", json={"network": "10.1.20.0/28", "name": "Printers"}, headers=as_user(agent)).json()
    assert [c["id"] for c in client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()["children"]] == [child["id"]]
    found = client.get("/api/ipam/addresses", params={"q": "10.1.20.2"}, headers=as_user(agent)).json()
    assert [(a["address"], a["subnet"]["name"]) for a in found] == [("10.1.20.2", "Printers")]

    # Big subnets: no map, no free count beyond a /16.
    big = client.post("/api/ipam/subnets", json={"network": "10.0.0.0/8"}, headers=as_user(agent)).json()
    assert big["usage"]["free"] is None and client.get(f"/api/ipam/subnets/{big['id']}", headers=as_user(agent)).json()["cells"] is None

    # Deleting a subnet keeps its addresses; only admins can.
    assert client.delete(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/ipam/subnets/{s['id']}", headers=as_user(admin)).status_code == 204
    assert len(client.get("/api/ipam/addresses", params={"q": "10.1.20."}, headers=as_user(agent)).json()) == 3


def test_addresses(client, db, agent):
    pc = Device(name="PC-0042")
    db.add(pc)
    db.commit()
    body = {"address": "10.1.30.15", "hostname": "pc-0042.corp.example", "mac": "AA-BB-CC-00-11-22", "kind": "reserved", "device_id": pc.id}
    r = client.post("/api/ipam/addresses", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["mac"] == "aa:bb:cc:00:11:22" and a["device"]["name"] == "PC-0042" and a["source"] == "manual" and a["subnet"] is None
    assert client.post("/api/ipam/addresses", json={"address": "10.1.30.15"}, headers=as_user(agent)).status_code == 409
    for bad in ({"address": "10.1.30.256"}, {"mac": "nope"}, {"device_id": 999999}, {"kind": "free"}):
        assert client.post("/api/ipam/addresses", json={**body, "address": "10.1.30.16", **bad}, headers=as_user(agent)).status_code == 422, bad

    # Found by start of address, CIDR, host name, MAC in any format, device.
    for q in ("10.1.30.", "10.1.30.0/24", "pc-0042", "aabb.cc00", "aa-bb-cc-00", "PC-00"):
        assert [x["id"] for x in client.get("/api/ipam/addresses", params={"q": q}, headers=as_user(agent)).json()] == [a["id"]], q
    assert client.get("/api/ipam/addresses", params={"q": "10.1.31."}, headers=as_user(agent)).json() == []
    assert client.get("/api/ipam/addresses", params={"device_id": pc.id}, headers=as_user(agent)).json()[0]["id"] == a["id"]

    r = client.put(f"/api/ipam/addresses/{a['id']}", json={**body, "kind": "static", "mac": ""}, headers=as_user(agent))
    assert r.json()["kind"] == "static" and r.json()["mac"] == ""
    # Technicians free addresses.
    assert client.delete(f"/api/ipam/addresses/{a['id']}", headers=as_user(agent)).status_code == 204


def _runner(client, admin, name, languages=("python",)):
    r = client.post("/api/runners", json={"name": name, "languages": list(languages)}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    rid = next(x["id"] for x in client.get("/api/runners", headers=as_user(admin)).json() if x["name"] == name)
    return rid, {"Authorization": f"Bearer {r.json()['token']}"}


def _result(hosts):
    return "Scanning...\n" + ipamscan.MARKER + json.dumps({"hosts": hosts}) + "\n"


def _scan(client, agent, subnet_id, runner, output, exit_code=0):
    r = client.post(f"/api/ipam/subnets/{subnet_id}/scan", headers=as_user(agent))
    assert r.status_code == 201, r.text
    job = client.post("/api/runner/claim", json={"host": "lan5", "version": "1.1"}, headers=runner)
    assert job.status_code == 200 and job.json()["values"] == {"network": "10.5.0.0/24"}
    run_id = job.json()["run_id"]
    client.post(f"/api/runner/runs/{run_id}/output", json={"text": output}, headers=runner)
    client.post(f"/api/runner/runs/{run_id}/finish", json={"exit_code": exit_code, "error": None}, headers=runner)
    return r.json()


def _open(client, agent, subnet_id):
    return {(f["address"], f["kind"]): f for f in client.get("/api/ipam/findings", params={"subnet_id": subnet_id}, headers=as_user(agent)).json()}


def test_scan_findings(client, db, agent, admin):
    lan5, lan5_auth = _runner(client, admin, "lan5")
    _, other_auth = _runner(client, admin, "elsewhere")
    s = client.post(
        "/api/ipam/subnets",
        json={"network": "10.5.0.0/24", "gateway": "10.5.0.1", "dhcp_start": "10.5.0.100", "dhcp_end": "10.5.0.199", "scan_runner_id": lan5, "scan_every_hours": 24},
        headers=as_user(agent),
    ).json()
    assert s["scan_runner_id"] == lan5 and s["last_scan"] is None
    assert [r["name"] for r in client.get("/api/ipam/runners", headers=as_user(agent)).json()] == ["elsewhere", "lan5"]
    add = lambda **b: client.post("/api/ipam/addresses", json=b, headers=as_user(agent)).json()
    srv, gone, dnu, prn = add(address="10.5.0.10", hostname="srv1", mac="aa:00:00:00:00:01"), add(address="10.5.0.20", hostname="old"), add(address="10.5.0.30", kind="do_not_use"), add(address="10.5.0.40", hostname="printer")
    db.get(IpAddress, gone["id"]).last_seen_at = sla.utcnow() - timedelta(days=40)
    db.commit()

    hosts = [
        {"ip": "10.5.0.1", "hostname": "gw"},
        {"ip": "10.5.0.10", "hostname": "SRV1.corp.example", "mac": "AA-00-00-00-00-02"},
        {"ip": "10.5.0.30", "hostname": ""},
        {"ip": "10.5.0.40", "hostname": "prn-40.corp.example", "mac": "aabb.cc00.0040"},
        {"ip": "10.5.0.50", "hostname": "mystery", "mac": "aa:00:00:00:00:50"},
        {"ip": "10.5.0.150", "hostname": "laptop"},  # in the DHCP pool: a lease
        {"ip": "10.9.9.9"},  # not in the subnet
    ]
    # Pinned to the subnet's runner: another runner doesn't get it.
    r = client.post(f"/api/ipam/subnets/{s['id']}/scan", headers=as_user(agent))
    assert r.status_code == 201
    assert client.post(f"/api/ipam/subnets/{s['id']}/scan", headers=as_user(agent)).status_code == 409
    assert client.post("/api/runner/claim", json={"host": "x", "version": "1.1"}, headers=other_auth).status_code == 204
    job = client.post("/api/runner/claim", json={"host": "lan5", "version": "1.1"}, headers=lan5_auth).json()
    client.post(f"/api/runner/runs/{job['run_id']}/output", json={"text": _result(hosts)}, headers=lan5_auth)
    client.post(f"/api/runner/runs/{job['run_id']}/finish", json={"exit_code": 0, "error": None}, headers=lan5_auth)

    got = client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()
    assert got["last_scan"]["status"] == "succeeded" and got["last_scan"]["hosts_seen"] == 6 and got["last_scan"]["runner"] == "lan5"
    assert got["open_findings"] == 5
    found = _open(client, agent, s["id"])
    assert set(found) == {("10.5.0.10", "mac_changed"), ("10.5.0.20", "not_seen"), ("10.5.0.30", "in_use_do_not_use"), ("10.5.0.40", "hostname_changed"), ("10.5.0.50", "new_host")}
    assert found[("10.5.0.10", "mac_changed")]["details"]["mac"] == "aa:00:00:00:00:02"
    by_addr = {a["address"]: a for a in got["addresses"]}
    # Seen: last seen set; empty MAC filled; the short name matched the FQDN, so no finding for srv1's name.
    assert by_addr["10.5.0.40"]["mac"] == "aa:bb:cc:00:00:40" and by_addr["10.5.0.40"]["hostname"] == "printer"
    assert by_addr["10.5.0.10"]["last_seen_at"] and by_addr["10.5.0.10"]["mac"] == "aa:00:00:00:00:01"

    act = lambda fid, what: client.post(f"/api/ipam/findings/{fid}/{what}", headers=as_user(agent))
    assert act(found[("10.5.0.50", "new_host")]["id"], "apply").json()["status"] == "applied"
    new = client.get("/api/ipam/addresses", params={"q": "10.5.0.50"}, headers=as_user(agent)).json()[0]
    assert (new["hostname"], new["mac"], new["source"]) == ("mystery", "aa:00:00:00:00:50", "scan")
    act(found[("10.5.0.40", "hostname_changed")]["id"], "apply")
    assert db.get(IpAddress, prn["id"]).hostname == "prn-40.corp.example"
    act(found[("10.5.0.10", "mac_changed")]["id"], "dismiss")
    assert act(found[("10.5.0.30", "in_use_do_not_use")]["id"], "apply").status_code == 409
    assert act(found[("10.5.0.10", "mac_changed")]["id"], "apply").status_code == 409

    # Same results again: the dismissed difference isn't raised again; open ones are refreshed, not duplicated.
    scan = _scan(client, agent, s["id"], lan5_auth, _result(hosts))
    assert set(_open(client, agent, s["id"])) == {("10.5.0.20", "not_seen"), ("10.5.0.30", "in_use_do_not_use")}
    assert client.get(f"/api/ipam/subnets/{s['id']}/scans", headers=as_user(agent)).json()[0]["new_findings"] == 0
    # .30 quiet now: that finding clears. Freeing the gone address deletes its record.
    _scan(client, agent, s["id"], lan5_auth, _result([h for h in hosts if h["ip"] != "10.5.0.30"]))
    found = _open(client, agent, s["id"])
    assert set(found) == {("10.5.0.20", "not_seen")}
    act(found[("10.5.0.20", "not_seen")]["id"], "apply")
    assert db.get(IpAddress, gone["id"]) is None

    # A failed run or output without results: the scan says why.
    _scan(client, agent, s["id"], lan5_auth, "ping: command not found\n", exit_code=1)
    assert client.get(f"/api/ipam/subnets/{s['id']}/scans", headers=as_user(agent)).json()[0]["error"]
    _scan(client, agent, s["id"], lan5_auth, "nothing\n")
    assert "no results" in client.get(f"/api/ipam/subnets/{s['id']}/scans", headers=as_user(agent)).json()[0]["error"]

    big = client.post("/api/ipam/subnets", json={"network": "10.6.0.0/16"}, headers=as_user(agent)).json()
    assert client.post(f"/api/ipam/subnets/{big['id']}/scan", headers=as_user(agent)).status_code == 409


def test_scheduled_scans(client, db, agent):
    s = client.post("/api/ipam/subnets", json={"network": "10.7.0.0/24", "scan_every_hours": 12}, headers=as_user(agent)).json()
    client.post("/api/ipam/subnets", json={"network": "10.8.0.0/24"}, headers=as_user(agent))
    assert ipamscan.run_due(db) == 1
    db.commit()
    # Not again while it's waiting, nor within 12 hours.
    assert ipamscan.run_due(db) == 0
    scan = db.scalar(select(IpScan).where(IpScan.subnet_id == s["id"]))
    assert scan.scheduled and scan.run.only_runner_id is None
    scan.run.status = RunStatus.failed
    db.commit()
    assert ipamscan.run_due(db) == 0
    scan.created_at = sla.utcnow() - timedelta(hours=13)
    db.commit()
    assert ipamscan.run_due(db) == 1


def test_device_addresses(client, db, agent):
    pc = Device(name="PC-0042", dns_hostname="pc-0042.corp.example")
    other = Device(name="PC-0099")
    db.add_all([pc, other])
    db.commit()
    client.post("/api/ipam/subnets", json={"network": "10.1.30.0/24", "name": "Staff"}, headers=as_user(agent))
    add = lambda **b: client.post("/api/ipam/addresses", json=b, headers=as_user(agent)).json()
    linked = add(address="10.1.30.15", device_id=pc.id)
    by_fqdn = add(address="10.1.30.16", hostname="PC-0042.corp.example")
    by_short = add(address="10.1.40.16", hostname="pc-0042")
    add(address="10.1.30.17", hostname="pc-00421")
    add(address="10.1.30.18", hostname="pc-0042", device_id=other.id)
    got = client.get(f"/api/ipam/devices/{pc.id}/addresses", headers=as_user(agent)).json()
    assert [(x["address"]["id"], x["linked"]) for x in got] == [(linked["id"], True), (by_fqdn["id"], False), (by_short["id"], False)]
    assert got[0]["address"]["subnet"]["name"] == "Staff" and got[2]["address"]["subnet"] is None
    r = client.post(f"/api/ipam/addresses/{by_fqdn['id']}/link-device/{pc.id}", headers=as_user(agent))
    assert r.json()["device"]["name"] == "PC-0042"
    assert [x["linked"] for x in client.get(f"/api/ipam/devices/{pc.id}/addresses", headers=as_user(agent)).json()] == [True, True, False]


def test_dhcp_dns_read(client, db, agent, admin, monkeypatch):
    from app.config import settings

    s = client.post("/api/ipam/subnets", json={"network": "10.9.0.0/24", "gateway": "10.9.0.1"}, headers=as_user(agent)).json()
    assert s["dhcp_dns"] is False
    r = client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "dhcp_dns"}, headers=as_user(agent))
    assert r.status_code == 409 and "Settings > IP addresses" in r.json()["detail"]
    monkeypatch.setattr(settings, "ipam_dhcp_server", "dc01.corp.example")
    monkeypatch.setattr(settings, "ipam_dns_server", "dc01.corp.example")
    monkeypatch.setattr(settings, "ipam_dns_zones", "corp.example")
    monkeypatch.setattr(settings, "ipam_windows_runner", "win1")
    assert client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "dhcp_dns"}, headers=as_user(agent)).json()["detail"] == "There's no active runner called 'win1' (Settings > IP addresses)"
    _, py_auth = _runner(client, admin, "lan9")
    win1, win_auth = _runner(client, admin, "win1", ("powershell",))

    docs = [
        ("10.9.0.10", "printer-2", "static", ""),  # DHCP has a reservation for it
        ("10.9.0.11", "fileserver", "static", "00:11:22:33:44:11"),  # DNS calls it nas01
        ("10.9.0.12", "", "dhcp", ""),  # host name and MAC filled from the lease
        ("10.9.0.13", "old-kiosk", "do_not_use", ""),
    ]
    for ip, host, kind, mac in docs:
        assert client.post("/api/ipam/addresses", json={"address": ip, "hostname": host, "kind": kind, "mac": mac}, headers=as_user(agent)).status_code == 201
    r = client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "dhcp_dns"}, headers=as_user(agent))
    assert r.status_code == 201 and r.json()["kind"] == "dhcp_dns"
    # The Python runner doesn't take it; the named PowerShell one does, with the servers.
    assert client.post("/api/runner/claim", json={"host": "lan9", "version": "1.1"}, headers=py_auth).status_code == 204
    job = client.post("/api/runner/claim", json={"host": "win1", "version": "1.1"}, headers=win_auth).json()
    assert job["language"] == "powershell" and job["values"] == {"network": "10.9.0.0/24", "dhcp_server": "dc01.corp.example", "dns_server": "dc01.corp.example", "dns_zones": "corp.example"}
    hosts = [
        {"ip": "10.9.0.10", "hostname": "printer-2.corp.example", "mac": "AA-BB-CC-00-00-10", "kind": "reserved", "active": True, "dns": ["printer-2.corp.example"]},
        {"ip": "10.9.0.11", "hostname": "nas01.corp.example", "mac": "", "kind": "", "active": False, "dns": ["nas01.corp.example"]},
        {"ip": "10.9.0.12", "hostname": "pc-0042.corp.example", "mac": "AA-BB-CC-00-00-12", "kind": "dhcp", "active": True, "dns": []},
        {"ip": "10.9.0.13", "hostname": "kiosk.corp.example", "mac": "", "kind": "", "active": False, "dns": ["kiosk.corp.example"]},
        {"ip": "10.9.0.20", "hostname": "cam-lobby", "mac": "AA-BB-CC-00-00-20", "kind": "reserved", "active": False, "dns": []},
        {"ip": "10.9.0.150", "hostname": "laptop-7", "mac": "AA-BB-CC-00-01-50", "kind": "dhcp", "active": True, "dns": ["laptop-7.corp.example"]},
        {"ip": "10.9.0.40", "hostname": "", "mac": "", "kind": "", "active": False, "dns": ["old-app.corp.example"]},
        # A non-Ethernet client ID is left out; outside the subnet is ignored.
        {"ip": "10.9.0.41", "hostname": "phone", "mac": "01-02", "kind": "reserved", "active": True, "dns": []},
        {"ip": "10.8.0.5", "hostname": "elsewhere", "mac": "", "kind": "", "active": False, "dns": ["elsewhere.corp.example"]},
    ]
    out = "Reading DHCP on dc01.corp.example\n" + ipamscan.MARKER + json.dumps({"network": "10.9.0.0/24", "scopes": {"scope": "10.9.0.0", "start": "10.9.0.100", "end": "10.9.0.199"}, "hosts": hosts}) + "\n"
    client.post(f"/api/runner/runs/{job['run_id']}/output", json={"text": out}, headers=win_auth)
    client.post(f"/api/runner/runs/{job['run_id']}/finish", json={"exit_code": 0, "error": None}, headers=win_auth)

    got = client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()
    # The empty pool is taken from the scope; a network scan hasn't run.
    assert (got["dhcp_start"], got["dhcp_end"], got["last_scan"], got["dhcp_dns"]) == ("10.9.0.100", "10.9.0.199", None, True)
    assert got["last_dhcp_dns"]["status"] == "succeeded" and got["last_dhcp_dns"]["hosts_seen"] == 8 and got["last_dhcp_dns"]["runner"] == "win1"
    rec = {a["address"]: a for a in got["addresses"]}
    # Leased: seen and filled. Only in DNS: not seen.
    assert (rec["10.9.0.12"]["hostname"], rec["10.9.0.12"]["mac"]) == ("pc-0042.corp.example", "aa:bb:cc:00:00:12") and rec["10.9.0.12"]["last_seen_at"]
    assert rec["10.9.0.11"]["last_seen_at"] is None and rec["10.9.0.10"]["mac"] == "aa:bb:cc:00:00:10"
    found = _open(client, agent, s["id"])
    assert set(found) == {
        ("10.9.0.10", "kind_changed"), ("10.9.0.11", "hostname_changed"), ("10.9.0.13", "in_use_do_not_use"), ("10.9.0.13", "hostname_changed"),
        ("10.9.0.20", "new_host"), ("10.9.0.40", "new_host"), ("10.9.0.41", "new_host"),
    }
    # The dynamic lease (laptop-7) isn't flagged, as pool hosts aren't in network scans.
    assert found[("10.9.0.41", "new_host")]["details"]["mac"] == ""
    assert found[("10.9.0.10", "kind_changed")]["details"]["documented_kind"] == "static" and found[("10.9.0.20", "new_host")]["scan_kind"] == "dhcp_dns"

    # Applying: the reservation is documented as reserved, with its MAC; a DNS-only host isn't "seen".
    for key in [("10.9.0.10", "kind_changed"), ("10.9.0.20", "new_host"), ("10.9.0.40", "new_host")]:
        assert client.post(f"/api/ipam/findings/{found[key]['id']}/apply", headers=as_user(agent)).status_code == 200
    a = {x["address"]: x for x in client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()["addresses"]}
    assert a["10.9.0.10"]["kind"] == "reserved"
    assert (a["10.9.0.20"]["kind"], a["10.9.0.20"]["mac"], a["10.9.0.20"]["last_seen_at"]) == ("reserved", "aa:bb:cc:00:00:20", None)
    assert (a["10.9.0.40"]["kind"], a["10.9.0.40"]["hostname"]) == ("static", "old-app.corp.example")

    # A network scan that sees nothing doesn't clear what the DHCP and DNS read found.
    net_scan = IpScan(subnet_id=s["id"], kind="network")
    db.add(net_scan)
    db.flush()
    ipamscan.apply_results(db, net_scan, [], sla.utcnow())
    db.commit()
    assert ("10.9.0.11", "hostname_changed") in _open(client, agent, s["id"])

    # Scheduled: both kinds when a server is set.
    client.put(f"/api/ipam/subnets/{s['id']}", json={**{k: got[k] for k in ("network", "name", "gateway", "dhcp_start", "dhcp_end", "dns_servers", "site", "notes")}, "vlan_id": None, "scan_every_hours": 6, "scan_runner_id": None}, headers=as_user(agent))
    db.query(IpScan).delete()
    db.commit()
    assert ipamscan.run_due(db) == 2
    assert sorted(x.kind for x in db.scalars(select(IpScan))) == ["dhcp_dns", "network"]


def test_dhcp_dns_script_output_shapes():
    # PowerShell writes a list of one as the one.
    data = ipamscan.parse_data("x\n" + ipamscan.MARKER + json.dumps({"hosts": {"ip": "10.0.0.1"}, "scopes": []}) + "\n")
    assert data["hosts"] == [{"ip": "10.0.0.1", "dns": []}] and data["scopes"] == []
    # Windows PowerShell 5.1's array wrapper.
    data = ipamscan.parse_data(ipamscan.MARKER + json.dumps({"hosts": {"value": [{"ip": "10.0.0.2", "dns": {"value": ["a.corp.example"], "Count": 1}}], "Count": 1}}))
    assert data["hosts"] == [{"ip": "10.0.0.2", "dns": ["a.corp.example"]}]
