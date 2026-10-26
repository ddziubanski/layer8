# SPDX-License-Identifier: AGPL-3.0-or-later
"""Relay deployment: the no-work check-in, the token-free kit and the installers' syntax."""
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import RunStatus, ScriptRun
from tests.conftest import as_user
from tests.test_runs import bearer, runner, script

RUNNER = Path(__file__).resolve().parents[2] / "runner"


def test_check_in_records_the_relay_and_claims_nothing(client, db, admin, agent):
    s = script(client, admin)
    token = runner(client, admin, name="relay01")
    # Queue a run the relay could take.
    queued = client.post(f"/api/scripts/{s['id']}/runs", json={"on_runner": True, "values": {}}, headers=as_user(admin))
    assert queued.status_code in (201, 422), queued.text
    before = {r.id: r.status for r in db.scalars(select(ScriptRun))}
    r = client.post("/api/runner/check-in", json={"host": "relay01.corp.local", "version": "1.4"}, headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "relay01" and set(r.json()["languages"]) == {"bash", "python", "eos"}
    db.expire_all()
    assert {r.id: r.status for r in db.scalars(select(ScriptRun))} == before
    assert all(status == RunStatus.queued for status in before.values())
    seen = client.get("/api/runners", headers=as_user(admin)).json()[0]
    assert (seen["last_host"], seen["last_version"]) == ("relay01.corp.local", "1.4") and seen["last_seen_at"]
    assert client.post("/api/runner/check-in", json={}, headers=bearer("hdr_wrong")).status_code == 401


def test_kit_is_admin_only_and_holds_no_tokens(client, db, admin, agent, monkeypatch):
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.corp.example")
    token = runner(client, admin)
    assert client.get("/api/runners/kit", headers=as_user(agent)).status_code == 403
    r = client.get("/api/runners/kit", headers=as_user(admin))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    kit = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(kit.namelist()) == sorted(["helpdesk_runner.py", "install-relay.sh", "Install-Layer8Relay.ps1", "New-Layer8RelayGpo.ps1",
                                             "Set-Layer8RelayToken.ps1", "relay.json", "README.txt"])
    assert json.loads(kit.read("relay.json")) == {"url": "https://helpdesk.corp.example"}
    everything = b"".join(kit.read(n) for n in kit.namelist())
    assert token.encode() not in everything and b"hdr_" + token[4:12].encode() not in everything


def test_installers_parse():
    assert subprocess.run(["sh", "-n", str(RUNNER / "install-relay.sh")]).returncode == 0
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", str(RUNNER / "install-relay.sh")]).returncode == 0


def test_installer_preview_changes_nothing(tmp_path):
    out = subprocess.run(["sh", str(RUNNER / "install-relay.sh"), "--url", "https://helpdesk.corp.example", "--token-file", str(tmp_path / "none")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "preview (nothing changes" in out.stdout and "/etc/helpdesk" in out.stdout
    for bad in (["--url", "http://helpdesk.corp.example", "--token-file", "x"], ["--url", "https://x.example"]):
        assert subprocess.run(["sh", str(RUNNER / "install-relay.sh"), *bad], capture_output=True).returncode != 0


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell isn't installed here (CI checks the scripts with it when present)")
def test_windows_scripts_parse():
    for ps1 in (RUNNER / "windows").glob("*.ps1"):
        check = f"$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('{ps1}', [ref]$null, [ref]$e) | Out-Null; exit $e.Count"
        assert subprocess.run(["pwsh", "-NoProfile", "-Command", check]).returncode == 0, ps1.name
