# SPDX-License-Identifier: AGPL-3.0-or-later
"""Relays and runs: requests, approval, the relay protocol, ticket logging, and the relay program itself."""

import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app import runs, sla
from app.models import Comment, Device, Queue, ScriptRun, Ticket, TicketEvent, TicketSource, UserRole
from tests.conftest import as_user, make_user

RUNNER_FILE = Path(__file__).resolve().parents[2] / "runner" / "helpdesk_runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("helpdesk_runner", RUNNER_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def script(client, admin, **over):
    body = {
        "name": "Echo", "kind": "action", "language": "bash", "body": 'echo "hello $HD_WHO on ${HD_TARGET:-runner}"',
        "parameters": [{"name": "who", "type": "text", "default": "world"}], "agents_can_run": True, **over,
    }
    r = client.post("/api/scripts", json=body, headers=as_user(admin))
    assert r.status_code == 201, r.text
    return r.json()


def runner(client, admin, languages=("bash", "python", "eos"), name="lab"):
    r = client.post("/api/runners", json={"name": name, "languages": list(languages)}, headers=as_user(admin))
    assert r.status_code == 201, r.text
    return r.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ticket(db, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="Printer", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    return t


def test_run_on_devices_from_a_ticket(client, db, admin, agent, ticket):
    s = script(client, admin)
    token = runner(client, admin)
    pc = Device(name="WS-0142", dns_hostname="ws-0142.corp.local", os="Windows 11")
    db.add(pc)
    db.commit()

    pre = client.post(f"/api/scripts/{s['id']}/preview", json={"device_ids": [pc.id], "values": {"who": "Ana"}}, headers=as_user(agent)).json()
    assert pre["targets"] == [{"device_id": pc.id, "name": "WS-0142", "target": "ws-0142.corp.local"}] and pre["runners"] == ["lab"] and not pre["needs_approval"]
    r = client.post(f"/api/scripts/{s['id']}/runs", json={"device_ids": [pc.id], "values": {"who": "Ana"}, "ticket_id": ticket.id}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    run_id = r.json()[0]["id"]
    assert r.json()[0]["status"] == "queued"

    assert client.post("/api/runner/claim", json={}, headers=bearer("hdr_wrong")).status_code == 401
    job = client.post("/api/runner/claim", json={"host": "runner1", "version": "1.0"}, headers=bearer(token)).json()
    assert job["run_id"] == run_id and job["target"] == "ws-0142.corp.local" and job["values"] == {"who": "Ana"} and job["target_os"] == "Windows 11"
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).status_code == 204
    client.post(f"/api/runner/runs/{run_id}/output", json={"text": "hello Ana\n"}, headers=bearer(token))
    assert client.post(f"/api/runner/runs/{run_id}/finish", json={"exit_code": 0}, headers=bearer(token)).status_code == 204
    # Not twice.
    assert client.post(f"/api/runner/runs/{run_id}/finish", json={"exit_code": 0}, headers=bearer(token)).status_code == 409

    detail = client.get(f"/api/runs/{run_id}", headers=as_user(agent)).json()
    assert (detail["status"], detail["output"], detail["runner_name"]) == ("succeeded", "hello Ana\n", "lab")
    notes = [c.body for c in db.query(Comment).filter_by(ticket_id=ticket.id).order_by(Comment.id)]
    assert notes[0].startswith('Ran script "Echo" (version 1) on ws-0142.corp.local')
    assert "finished (exit code 0)" in notes[1] and notes[1].endswith("hello Ana")
    assert db.query(TicketEvent).filter_by(ticket_id=ticket.id, type="script_run").count() == 1
    assert [x["id"] for x in client.get("/api/runs", params={"ticket_id": ticket.id}, headers=as_user(agent)).json()] == [run_id]


def test_high_risk_needs_another_admin(client, db, admin, agent):
    s = script(client, admin, name="Wipe", high_risk=True)
    token = runner(client, admin)
    r = client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True}, headers=as_user(admin))
    run_id = r.json()[0]["id"]
    assert r.json()[0]["status"] == "awaiting_approval" and r.json()[0]["can_decide"] is False
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).status_code == 204
    assert "Someone else" in client.post(f"/api/runs/{run_id}/approve", headers=as_user(admin)).json()["detail"]
    assert client.post(f"/api/runs/{run_id}/approve", headers=as_user(agent)).status_code == 403
    other = make_user(db, "admin2@corp.local", UserRole.admin, "Second Admin")
    db.commit()
    assert client.post(f"/api/runs/{run_id}/approve", headers=as_user(other)).json()["status"] == "queued"
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).json()["target"] is None

    # Rejected and cancelled runs never start.
    r1 = client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True}, headers=as_user(admin)).json()[0]
    assert client.post(f"/api/runs/{r1['id']}/reject", headers=as_user(other)).json()["status"] == "rejected"
    r2 = client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True}, headers=as_user(admin)).json()[0]
    assert client.post(f"/api/runs/{r2['id']}/cancel", headers=as_user(admin)).json()["status"] == "cancelled"
    assert client.post("/api/runner/claim", json={}, headers=bearer(token)).status_code == 204


def test_targets_rules_and_languages(client, db, admin, agent):
    s = script(client, admin)
    ps = script(client, admin, name="PS", language="powershell", body="Get-Date", parameters=[], agents_can_run=False)
    ps_only = runner(client, admin, ("powershell",), name="win")
    bash_only = runner(client, admin, ("bash",), name="linux")
    db.add_all([Device(name="A", is_active=True), Device(name="B", is_active=True, assigned_user_id=None)])
    db.commit()
    group = client.post("/api/scripts", json={"name": "All", "kind": "query", "language": "sql", "body": "SELECT id AS device_id FROM devices", "agents_can_run": True}, headers=as_user(admin)).json()
    r = client.post(f"/api/scripts/{s['id']}/runs", json={"group_script_id": group["id"]}, headers=as_user(agent))
    assert sorted(x["target"] for x in r.json()) == ["A", "B"]
    not_group = client.post("/api/scripts", json={"name": "Names", "kind": "query", "language": "sql", "body": "SELECT name FROM devices"}, headers=as_user(admin)).json()
    assert client.post(f"/api/scripts/{s['id']}/runs", json={"group_script_id": not_group["id"]}, headers=as_user(admin)).status_code == 422
    assert "Pick the devices" in client.post(f"/api/scripts/{s['id']}/runs", json={}, headers=as_user(agent)).json()["detail"]
    # Agents only run what they're allowed to.
    assert client.post(f"/api/scripts/{ps['id']}/runs", json={"on_runner": True}, headers=as_user(agent)).status_code == 422
    client.post(f"/api/scripts/{ps['id']}/runs", json={"on_runner": True}, headers=as_user(admin))
    # Each runner only gets its languages.
    assert client.post("/api/runner/claim", json={}, headers=bearer(ps_only)).json()["language"] == "powershell"
    assert client.post("/api/runner/claim", json={}, headers=bearer(bash_only)).json()["language"] == "bash"


def test_new_token_and_stale_runs(client, db, admin):
    s = script(client, admin)
    old = runner(client, admin)
    rid = client.get("/api/runners", headers=as_user(admin)).json()[0]["id"]
    new = client.post(f"/api/runners/{rid}/token", headers=as_user(admin)).json()["token"]
    assert client.post("/api/runner/claim", json={}, headers=bearer(old)).status_code == 401
    client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True}, headers=as_user(admin))
    job = client.post("/api/runner/claim", json={"host": "r1"}, headers=bearer(new)).json()
    assert client.get("/api/runners", headers=as_user(admin)).json()[0]["last_host"] == "r1"
    later = sla.utcnow() + timedelta(seconds=runs.TIME_LIMIT_SECONDS) + runs.STALE_GRACE + timedelta(seconds=1)
    assert runs.fail_stale(db, later) == 1
    db.commit()
    run = db.get(ScriptRun, job["run_id"])
    assert run.status == "failed" and "stopped answering" in run.error


# The runner program, against the API through the test client


class ClientApi:
    def __init__(self, client, token):
        self.client, self.token = client, token

    def post(self, path, body):
        r = client_post(self.client, path, body, self.token)
        return r.status_code, (r.json() if r.content else None)


def client_post(client, path, body, token):
    return client.post(path, json=body, headers=bearer(token))


class Args:
    powershell = None
    eos_user = "admin"
    eos_password_file = None
    eos_ca_file = None
    eos_insecure = True


def run_one(client, token, mod):
    api = ClientApi(client, token)
    status, job = api.post("/api/runner/claim", {"host": "t", "version": mod.VERSION})
    assert status == 200
    mod.handle(api, job, Args)
    return job["run_id"]


def test_runner_program_runs_bash_and_python(client, db, admin, ticket):
    mod = load_runner()
    token = runner(client, admin)
    s = script(client, admin)
    client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True, "values": {"who": "Ana"}, "ticket_id": ticket.id}, headers=as_user(admin))
    run_id = run_one(client, token, mod)
    assert db.get(ScriptRun, run_id).output == "hello Ana on runner\n"
    db.expire_all()
    assert db.get(ScriptRun, run_id).status == "succeeded"

    py = script(client, admin, name="Py", language="python", body="import os, sys\nprint('days', os.environ['HD_DAYS'])\nsys.exit(3)", parameters=[{"name": "days", "type": "int", "default": 7}])
    client.post(f"/api/scripts/{py['id']}/runs", json={"on_runner": True}, headers=as_user(admin))
    run_id = run_one(client, token, mod)
    db.expire_all()
    run = db.get(ScriptRun, run_id)
    assert (run.status, run.exit_code, run.output) == ("failed", 3, "days 7\n")


def test_runner_program_time_limit(client, db, admin, monkeypatch):
    mod = load_runner()
    token = runner(client, admin)
    s = script(client, admin, name="Slow", body="echo start; sleep 30; echo never", parameters=[])
    client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True}, headers=as_user(admin))
    monkeypatch.setattr(runs, "TIME_LIMIT_SECONDS", 1)
    run_id = run_one(client, token, mod)
    db.expire_all()
    run = db.get(ScriptRun, run_id)
    assert run.status == "failed" and "time limit" in run.error and run.output.startswith("start") and "never" not in run.output


def test_runner_program_eos_and_powershell_launcher(client, db, admin, tmp_path, monkeypatch):
    mod = load_runner()
    token = runner(client, admin)
    sw = Device(name="SW-CORE-1", dns_hostname="sw-core-1.corp.example")
    db.add(sw)
    db.commit()
    s = script(client, admin, name="Bounce", language="eos", body="! bounce a port\ninterface $port\nshutdown\nno shutdown", parameters=[{"name": "port", "type": "text", "required": True}])
    client.post(f"/api/scripts/{s['id']}/runs", json={"device_ids": [sw.id], "values": {"port": "Ethernet7"}}, headers=as_user(admin))
    (tmp_path / "pw").write_text("secret\n")
    Args.eos_password_file = str(tmp_path / "pw")
    sent = {}

    class Answer:
        def __init__(self, data):
            self.data = data

        def read(self):
            return json.dumps(self.data).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        sent["url"], sent["body"], sent["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
        return Answer({"jsonrpc": "2.0", "id": 1, "result": [{"output": ""}, {"output": ""}, {"output": ""}]})

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    run_id = run_one(client, token, mod)
    assert sent["url"] == "https://sw-core-1.corp.example/command-api"
    assert sent["body"]["params"]["cmds"] == ["interface Ethernet7", "shutdown", "no shutdown"]
    db.expire_all()
    assert db.get(ScriptRun, run_id).status == "succeeded"

    # PowerShell isn't on this test machine: check what the launcher would run.
    outer = mod.powershell_files({"run_id": 1, "body": "Get-Service $name", "target": "ws-1.corp.local", "values": {"name": "Spooler"}}, tmp_path)
    text = outer.read_text(encoding="utf-8-sig")
    assert "Invoke-Command -ComputerName $HD.target -FilePath $inner" in text
    assert (tmp_path / "script.ps1").read_text(encoding="utf-8-sig").endswith("Get-Service $name\n")


def test_runs_from_restricted_tickets_stay_private(client, db, admin, agent, requester):
    hr = Queue(name="HR", restricted=True)
    db.add(hr)
    db.flush()
    t = Ticket(subject="Payroll laptop", source=TicketSource.portal, queue_id=hr.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    s = script(client, admin)
    client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True, "ticket_id": t.id}, headers=as_user(admin))
    assert client.get("/api/runs", headers=as_user(agent)).json() == []
    assert len(client.get("/api/runs", headers=as_user(admin)).json()) == 1
