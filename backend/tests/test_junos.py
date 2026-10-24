# SPDX-License-Identifier: AGPL-3.0-or-later
"""Juniper switches: the switch list, sealed credentials, the runner script against a fake Junos
REST API over HTTPS, storing what it read, uplinks, unreachable switches and the scheduled path."""
import datetime as dt
import json
import ssl
import subprocess
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import junos, sla
from app.config import settings
from app.models import NetworkInventory, NetworkObservation, NetworkOutage, NetworkRead, ScriptRun, Ticket
from tests.conftest import as_user
from tests.test_runs import bearer, runner


def d(v):
    return [{"data": v}]


# What an EX switch running ELS answers (trimmed), and an older non-ELS switching table.
SOFTWARE = {"software-information": [{"host-name": d("core-1"), "product-model": d("ex4300-48p"), "junos-version": d("21.4R3-S5")}]}
ARP = {"arp-table-information": [{"arp-table-entry": [
    {"mac-address": d("aa:bb:cc:00:00:01"), "ip-address": d("10.0.10.21"), "interface-name": d("irb.10")},
    {"mac-address": d("aa:bb:cc:00:00:02"), "ip-address": d("10.0.10.22"), "interface-name": d("irb.10")},
]}]}
ELS = {"l2ng-l2ald-rtb-macdb": [{"l2ng-l2ald-mac-entry-vlan": [{"l2ng-mac-entry": [
    {"l2ng-l2-mac-vlan-name": d("staff"), "l2ng-l2-mac-address": d("aa:bb:cc:00:00:01"), "l2ng-l2-mac-logical-interface": d("ge-0/0/5.0")},
    {"l2ng-l2-mac-vlan-name": d("staff"), "l2ng-l2-mac-address": d("aa:bb:cc:00:00:02"), "l2ng-l2-mac-logical-interface": d("ge-0/0/6.0")},
    {"l2ng-l2-mac-vlan-name": d("staff"), "l2ng-l2-mac-address": d("aa:bb:cc:00:00:09"), "l2ng-l2-mac-logical-interface": d("ae0.0")},
]}]}]}
OLD = {"ethernet-switching-table-information": [{"ethernet-switching-table": [{"mac-table-entry": [
    {"mac-vlan": d("guests"), "mac-address": d("aa:bb:cc:00:00:03"), "mac-interfaces-list": [{"mac-interfaces": d("ge-0/0/7.0")}]},
]}]}]}


@pytest.fixture(scope="module")
def junos_server(tmp_path_factory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("junos")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Accept")))
            answers = {"/rpc/get-software-information": SOFTWARE, "/rpc/get-arp-table-information": ARP,
                       "/rpc/get-ethernet-switching-table-information": ELS}
            body = json.dumps(answers.get(self.path, {})).encode()
            self.send_response(200 if self.path in answers else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"port": server.server_address[1], "ca": (folder / "cert.pem").read_text(), "seen": seen}
    server.shutdown()


def run_script(secrets: dict) -> list[dict]:
    done = subprocess.run([sys.executable, "-c", junos.SCRIPT_BODY], input=json.dumps(secrets), capture_output=True, text=True, timeout=60)
    assert "LAYER8-JUNOS-DONE" in done.stdout, done.stdout + done.stderr
    return junos.parse(done.stdout)


def test_switch_list_format():
    assert junos.parse_switches("core-1 10.0.0.2\n# comment\nidf2-sw sw2.corp.example:8443") == [
        {"name": "core-1", "host": "10.0.0.2", "port": 3443}, {"name": "idf2-sw", "host": "sw2.corp.example", "port": 8443}]
    for bad in ("core-1", "core 1 10.0.0.2", "a 10.0.0.2\nA 10.0.0.3", "x 10.0.0.2:99999", "x bad_host!"):
        with pytest.raises(junos.JunosError):
            junos.parse_switches(bad)


def test_runner_script_reads_a_junos_rest_api_over_verified_https(junos_server):
    secrets = {"username": "l8-read", "password": "s3cret", "verify": True, "ca": junos_server["ca"],
               "switches": [{"name": "core-1", "host": "localhost", "port": junos_server["port"]},
                            {"name": "gone", "host": "localhost", "port": 1}]}
    out = {s["name"]: s for s in run_script(secrets)}
    core = out["core-1"]
    assert core["ok"] and (core["hostname"], core["model"], core["version"]) == ("core-1", "ex4300-48p", "21.4R3-S5")
    assert ["aa:bb:cc:00:00:01", "10.0.10.21", "irb.10"] in core["arp"]
    assert ["aa:bb:cc:00:00:01", "staff", "ge-0/0/5.0"] in core["macs"]
    assert out["gone"]["ok"] is False and out["gone"]["error"]
    path, auth, accept = junos_server["seen"][0]
    assert path.startswith("/rpc/") and auth.startswith("Basic ") and accept == "application/json"
    # Without the CA the certificate isn't trusted, so nothing is read.
    assert run_script({**secrets, "ca": ""})[0]["ok"] is False


def test_older_switching_table_format_is_read():
    # The script's own helpers, on a non-ELS switch's answer.
    body = junos.SCRIPT_BODY
    helpers = {}
    exec(body[body.index("def val("):body.index("for sw in s[\"switches\"]:")], helpers)
    found = helpers["rows"](OLD, ("l2ng-l2-mac-address", "mac-address"))
    assert helpers["val"](found[0], "l2ng-l2-mac-address", "mac-address") == "aa:bb:cc:00:00:03"
    assert helpers["val"](found[0], "l2ng-l2-mac-vlan-name", "mac-vlan") == "guests"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "junos_enabled", True)
    monkeypatch.setattr(settings, "junos_switches", "core-1 10.0.0.2\nidf-2 10.0.0.3")
    monkeypatch.setattr(settings, "junos_username", "l8-read")
    monkeypatch.setattr(settings, "junos_password", "s3cret")
    monkeypatch.setattr(settings, "junos_uplink_macs", 3)


def test_apply_joins_arp_skips_uplinks_and_keeps_unreachable_switches_data(db, configured, service_desk, monkeypatch):
    macs = [["aa:bb:cc:00:00:01", "staff", "ge-0/0/5.0"], ["aa:bb:cc:00:00:02", "staff", "ge-0/0/6.0"]] + [[f"aa:bb:cc:00:01:{i:02x}", "staff", "xe-0/1/0.0"] for i in range(5)]
    first = [{"name": "core-1", "host": "10.0.0.2", "ok": True, "hostname": "core-1", "model": "ex4300", "version": "21.4", "arp": ARP_ROWS, "macs": macs},
             {"name": "idf-2", "host": "10.0.0.3", "ok": True, "hostname": "idf-2", "arp": [], "macs": [["aa:bb:cc:00:00:03", "guests", "ge-0/0/1.0"]]}]
    summary = junos.apply(db, first)
    db.commit()
    assert summary == "2 of 2 switches answered, 3 clients"
    o = db.get(NetworkObservation, ("junos", "core-1", "aa:bb:cc:00:00:01"))
    assert (o.port, o.network, o.address, o.uplink_name) == ("ge-0/0/5", "staff", "10.0.10.21", "core-1")
    assert db.query(NetworkObservation).filter(NetworkObservation.port == "xe-0/1/0").count() == 0  # uplink
    # idf-2 stops answering: it goes offline, its clients stay; after the grace period a ticket opens.
    monkeypatch.setattr(settings, "junos_outage_tickets", True)
    second = [first[0], {"name": "idf-2", "host": "10.0.0.3", "ok": False, "error": "TimeoutError"}]
    assert "no answer from idf-2" in junos.apply(db, second)
    db.commit()
    assert db.get(NetworkInventory, ("junos", "idf-2", "idf-2")).state == "offline"
    assert db.get(NetworkObservation, ("junos", "idf-2", "aa:bb:cc:00:00:03")) is not None
    db.get(NetworkOutage, ("junos", "idf-2", "idf-2")).down_since = sla.utcnow() - timedelta(minutes=20)
    db.commit()
    junos.apply(db, second)
    db.commit()
    assert db.query(Ticket).one().subject == "Offline: Juniper switch idf-2"


ARP_ROWS = [["aa:bb:cc:00:00:01", "10.0.10.21", "irb.10"], ["aa:bb:cc:00:00:02", "10.0.10.22", "irb.10"]]


def test_read_seals_credentials_and_a_runner_completes_it(client, db, admin, configured):
    token = runner(client, admin, languages=("python",))
    assert client.post("/api/junos/read", headers=as_user(admin)).status_code == 200
    assert client.post("/api/junos/read", headers=as_user(admin)).status_code == 409  # one at a time
    run = db.query(ScriptRun).one()
    assert run.values == {} and b"s3cret" not in (run.sealed or b"")
    job = client.post("/api/runner/claim", json={}, headers=bearer(token)).json()
    assert job["secret_values"]["username"] == "l8-read" and job["secret_values"]["switches"][0]["name"] == "core-1"
    output = "\n".join("LAYER8-JUNOS " + json.dumps(s) for s in [
        {"name": "core-1", "host": "10.0.0.2", "ok": True, "hostname": "core-1", "arp": ARP_ROWS, "macs": [["aa:bb:cc:00:00:01", "staff", "ge-0/0/5.0"]]},
        {"name": "idf-2", "host": "10.0.0.3", "ok": True, "macs": []}]) + "\nLAYER8-JUNOS-DONE\n"
    assert client.post(f"/api/runner/runs/{job['run_id']}/output", json={"text": output}, headers=bearer(token)).status_code in (200, 204)
    assert client.post(f"/api/runner/runs/{job['run_id']}/finish", json={"exit_code": 0}, headers=bearer(token)).status_code in (200, 204)
    db.expire_all()
    read = db.query(NetworkRead).one()
    assert read.summary.startswith("2 of 2 switches answered, 1 clients")
    status = client.get("/api/junos/status", headers=as_user(admin)).json()
    assert status["clients"] == 1 and status["switches"] == 2 and status["waiting"] is False


def test_scheduled_read_runs_as_the_automation_identity(client, db, admin, configured):
    token = runner(client, admin, languages=("python",))
    said, did = junos.scheduled_read(db)
    db.commit()
    assert did and "Asked for a Juniper read" in said
    job = client.post("/api/runner/claim", json={}, headers=bearer(token))
    assert job.status_code == 200 and job.json()["run_id"] == db.query(NetworkRead).one().run_id
