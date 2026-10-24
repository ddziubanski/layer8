# SPDX-License-Identifier: AGPL-3.0-or-later
"""SNMP discovery: sealed communities on the relay's stdin, gear and its neighbours and MAC
tables from the output, switch ports on addresses, and the /api/network endpoints."""

import json

import pytest
from sqlalchemy import select

from app import ipamscan, netdisco, runs, sla
from app.config import settings
from app.models import Device, IpScan, ScanKind, ScriptRun
from tests.conftest import as_user
from tests.test_runs import ClientApi, load_runner, runner


def gear(ip="10.5.0.2", **over):
    h = {
        "ip": ip, "sys_descr": "Meraki MS225-48FP Cloud Managed PoE Switch", "sys_object_id": "1.3.6.1.4.1.29671.2.105", "uptime": 1234567,
        "contact": "netops@example.com", "sys_name": "sw-core-01", "location": "HQ IDF-1", "model": "MS225-48FP", "serial": "Q2XX-ABCD-1234", "kind": "switch",
        "neighbors": [
            {"local_port": "Gi1/0/2", "name": "SEP001122334455", "port": "00:11:22:33:44:55", "port_desc": "SW PORT", "chassis": "00:11:22:33:44:55", "address": None},
            {"local_port": "Te1/1/1", "name": "sw-access-02", "port": "port 1", "port_desc": "Port 1", "chassis": "00:18:0a:11:22:33", "address": "10.5.0.3"},
        ],
        "macs": [
            ["001122334455", "Gi1/0/2", 20], ["aabbcc000001", "Gi1/0/1", 20], ["aabbcc000002", "Gi1/0/2", 20],
            ["aabbcc0000a1", "Te1/1/1", 20], ["aabbcc0000a2", "Te1/1/1", 20], ["aabbcc000010", "Gi1/0/10", 30],
        ],
    }
    return {**h, **over}


def output(*hosts, split=False):
    lines = ["Asking 254 addresses in 10.5.0.0/24 over SNMP"]
    for h in hosts:
        macs = h["macs"]
        lines.append(netdisco.HOST + json.dumps({**h, "macs": macs[:2] if split else macs}))
        if split:
            lines.append(netdisco.MACS + json.dumps({"ip": h["ip"], "macs": macs[2:]}))
    lines.append(netdisco.DONE + json.dumps({"network": "10.5.0.0/24", "asked": 254, "answered": len(hosts)}))
    return "\n".join(lines) + "\n"


@pytest.fixture
def snmp(monkeypatch):
    monkeypatch.setattr(settings, "snmp_communities", "wrong, s3cret")


def test_runner_hands_secrets_to_python_on_stdin(client, db, admin):
    mod = load_runner()
    token = runner(client, admin)
    s = client.post("/api/scripts", json={"name": "Py secret", "kind": "action", "language": "python", "body": "import json, os, sys\nd = json.load(sys.stdin)\nprint(d['communities'], 'HD_COMMUNITIES' in os.environ)", "parameters": []}, headers=as_user(admin)).json()
    from app.models import Script

    (run,) = runs.request(db, db.get(Script, s["id"]), admin, device_ids=[], on_runner=True, values={}, ticket=None, sealed={"communities": "s3cret"})
    db.commit()
    api = ClientApi(client, token)
    status, job = api.post("/api/runner/claim", {"host": "t", "version": mod.VERSION})
    assert status == 200 and mod.VERSION >= "1.2"
    mod.handle(api, job, type("A", (), {"powershell": None})())
    db.expire_all()
    assert db.get(ScriptRun, run.id).output == "s3cret False\n"


def test_discovery(client, db, agent, admin, snmp):
    s = client.post("/api/ipam/subnets", json={"network": "10.5.0.0/24"}, headers=as_user(agent)).json()
    assert s["snmp"] is True and s["last_snmp"] is None
    client.post("/api/ipam/addresses", json={"address": "10.5.0.2"}, headers=as_user(agent))
    client.post("/api/ipam/addresses", json={"address": "10.5.0.51", "hostname": "pc-0051", "mac": "AA-BB-CC-00-00-01"}, headers=as_user(agent))
    client.post("/api/ipam/addresses", json={"address": "10.5.0.52", "hostname": "uplinked", "mac": "aa:bb:cc:00:00:a1"}, headers=as_user(agent))
    client.post("/api/ipam/addresses", json={"address": "10.5.0.53", "hostname": "pc-behind-phone", "mac": "aa:bb:cc:00:00:02"}, headers=as_user(agent))
    asset = Device(name="SW-CORE-01", serial="")
    db.add(asset)
    db.commit()

    r = client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "snmp"}, headers=as_user(agent))
    assert r.status_code == 201 and r.json()["kind"] == "snmp"
    assert client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "snmp"}, headers=as_user(agent)).json()["detail"] == "An SNMP discovery of this subnet is already waiting or running"
    token = runner(client, admin, ("python",))
    job = client.post("/api/runner/claim", json={"host": "t", "version": "1.2"}, headers={"Authorization": f"Bearer {token}"}).json()
    # The communities go sealed, never as a value.
    assert job["values"] == {"network": "10.5.0.0/24"} and job["secret_values"] == {"communities": "wrong,s3cret"}
    assert "s3cret" not in json.dumps(db.get(ScriptRun, job["run_id"]).values)
    auth = {"Authorization": f"Bearer {token}"}
    client.post(f"/api/runner/runs/{job['run_id']}/output", json={"text": output(gear(), gear("10.9.9.9"), split=True)}, headers=auth)
    client.post(f"/api/runner/runs/{job['run_id']}/finish", json={"exit_code": 0, "error": None}, headers=auth)

    got = client.get(f"/api/ipam/subnets/{s['id']}", headers=as_user(agent)).json()
    assert got["last_snmp"]["status"] == "succeeded" and got["last_snmp"]["hosts_seen"] == 1
    addrs = {a["address"]: a for a in got["addresses"]}
    # The gear's own address is seen and named; PCs get their switch port (edge port, not the uplink).
    assert addrs["10.5.0.2"]["hostname"] == "sw-core-01" and addrs["10.5.0.2"]["last_seen_at"]
    assert addrs["10.5.0.51"]["switch_port"] | {"seen_at": None} == {"switch_id": addrs["10.5.0.51"]["switch_port"]["switch_id"], "switch": "sw-core-01", "port": "Gi1/0/1", "vlan": 20, "seen_at": None, "shared": False}
    assert addrs["10.5.0.53"]["switch_port"]["port"] == "Gi1/0/2" and addrs["10.5.0.53"]["switch_port"]["shared"] is True

    nds = client.get("/api/network/devices", headers=as_user(agent)).json()
    assert len(nds) == 1
    nd = nds[0]
    assert (nd["vendor"], nd["model"], nd["serial"], nd["kind"], nd["uptime_seconds"], nd["neighbor_count"], nd["mac_count"]) == ("Cisco Meraki", "MS225-48FP", "Q2XX-ABCD-1234", "switch", 1234567, 2, 6)
    # Matched to the asset by name.
    assert nd["device"]["id"] == asset.id
    detail = client.get(f"/api/network/devices/{nd['id']}", headers=as_user(agent)).json()
    assert [p["port"] for p in detail["ports"]] == ["Gi1/0/1", "Gi1/0/2", "Gi1/0/10", "Te1/1/1"]
    assert detail["ports"][0]["macs"][0] | {"device": None} == {"mac": "aa:bb:cc:00:00:01", "vlan": 20, "address": "10.5.0.51", "hostname": "pc-0051", "device": None}
    assert detail["ports"][3]["neighbor"] == "sw-access-02" and detail["neighbors"][1]["remote_address"] == "10.5.0.3"
    # The phone's port isn't an uplink (sw-access-02 isn't discovered gear here, and two MACs aren't many).
    assert [p["uplink"] for p in detail["ports"]] == [False, False, False, False]
    assert client.get("/api/network/where", params={"mac": "aabb.cc00.0010"}, headers=as_user(agent)).json()["port"] == "Gi1/0/10"
    assert client.get("/api/network/where", params={"mac": "aa:bb:cc:99:99:99"}, headers=as_user(agent)).json() is None

    # A later discovery replaces the MAC table and neighbours.
    scan = IpScan(subnet_id=s["id"], kind=ScanKind.snmp)
    db.add(scan)
    db.flush()
    netdisco.apply_results(db, scan, netdisco.parse(output(gear(neighbors=[], macs=[["aabbcc000001", "Gi1/0/5", 20]]))), sla.utcnow())
    db.commit()
    detail = client.get(f"/api/network/devices/{nd['id']}", headers=as_user(agent)).json()
    assert detail["neighbors"] == [] and [p["port"] for p in detail["ports"]] == ["Gi1/0/5"]

    # Unlink, then forget (needs the IPAM delete permission).
    assert client.patch(f"/api/network/devices/{nd['id']}", json={"device_id": None}, headers=as_user(agent)).json()["device"] is None
    assert client.delete(f"/api/network/devices/{nd['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/network/devices/{nd['id']}", headers=as_user(admin)).status_code == 204


def test_discovery_needs_a_community_and_a_small_subnet(client, db, agent, monkeypatch):
    s = client.post("/api/ipam/subnets", json={"network": "10.6.0.0/21", "scan_every_hours": 6}, headers=as_user(agent)).json()
    r = client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "snmp"}, headers=as_user(agent))
    assert r.status_code == 409 and "No SNMP community" in r.json()["detail"]
    monkeypatch.setattr(settings, "snmp_communities", "public")
    assert "too big to discover" in client.post(f"/api/ipam/subnets/{s['id']}/scan", params={"kind": "snmp"}, headers=as_user(agent)).json()["detail"]
    small = client.post("/api/ipam/subnets", json={"network": "10.7.0.0/24", "scan_every_hours": 6}, headers=as_user(agent)).json()
    ipamscan.run_due(db)
    db.commit()
    kinds = sorted((x.subnet_id, str(x.kind)) for x in db.scalars(select(IpScan)))
    assert kinds == [(s["id"], "network"), (small["id"], "network"), (small["id"], "snmp")]


def test_output_problems():
    with pytest.raises(netdisco.DiscoveryError, match="no results line"):
        netdisco.parse("No SNMP community reached this script\n")
    assert netdisco.vendor_of("1.3.6.1.4.1.9.1.2066") == "Cisco" and netdisco.vendor_of("1.3.6.1.4.1.99999.1") == "" and netdisco.vendor_of("") == ""
    assert netdisco.parse(netdisco.HOST + "{broken\n" + netdisco.DONE + "{}\n") == []
