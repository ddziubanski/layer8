# SPDX-License-Identifier: AGPL-3.0-or-later
import io
import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import VERSION, update_protocol as protocol
from app.api import updates
from app.config import settings
from app.models import AppSetting
from tests.conftest import as_user

# A release newer than this one, so the tests keep working as VERSION moves on.
NEXT = f"{VERSION.split('.')[0]}.{int(VERSION.split('.')[1]) + 1}.0"
MANIFEST = {"format": 1, "repository": protocol.REPOSITORY, "version": "1.2.0", "minimum_version": "1.1.0", "platform": "debian13-amd64", "python": "3.13", "package": "layer8-v1.2.0-update.tar.gz", "size": 50, "sha256": "a" * 64}


@pytest.fixture
def signed(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("OpenSSL signature verification runs on the Linux CI host")
    key = Ed25519PrivateKey.generate()
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "release.pem").write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, keys


def test_manifest_signature_and_review_digest(signed):
    key, keys = signed
    raw = json.dumps(MANIFEST).encode()
    assert protocol.verify_manifest(raw, key.sign(raw), "v1.2.0", keys) == MANIFEST
    for signature in (b"x" * 64, key.sign(raw + b" ")):
        with pytest.raises(protocol.UpdateError, match="signature"):
            protocol.verify_manifest(raw, signature, "v1.2.0", keys)
    with pytest.raises(protocol.UpdateError):
        protocol.verify_manifest(raw, key.sign(raw), "v1.3.0", keys)


@pytest.mark.parametrize("over", [{"repository": "other/repo"}, {"package": "../../run.sh"}, {"size": 0}, {"size": protocol.MAX_PACKAGE + 1}, {"sha256": "bad"}, {"format": 2}, {"python": "3.12"}, {"platform": "other"}, {"command": "id"}])
def test_even_signed_manifests_obey_the_fixed_protocol(signed, over):
    key, keys = signed
    raw = json.dumps({**MANIFEST, **over}).encode()
    with pytest.raises(protocol.UpdateError):
        protocol.verify_manifest(raw, key.sign(raw), "v1.2.0", keys)


def test_missing_key_fails_closed(tmp_path):
    with pytest.raises(protocol.UpdateError, match="trusted"):
        protocol.verify_manifest(b"{}", b"x" * 64, "1.2.0", tmp_path)


@pytest.mark.parametrize("value", ["main", "v1.2.0-beta.1", "1.02.0", "v1.2.0/../../x", "1.2", None])
def test_only_stable_versions(value):
    with pytest.raises(protocol.UpdateError):
        protocol.version(value)


def test_semver_order_and_no_downgrades(monkeypatch):
    monkeypatch.setattr(protocol, "releases", lambda: [{"tag_name": "v1.9.0"}, {"tag_name": "v1.10.0"}, {"tag_name": "main"}])
    assert protocol.latest()["tag_name"] == "v1.10.0"
    for current in ("1.2.0", "2.0.0", "1.0.0"):
        with pytest.raises(protocol.UpdateError):
            protocol.compatible(MANIFEST, current)


@pytest.mark.parametrize("url", ["http://github.com/a", "https://evil.example/x", "https://github.com.evil.example", "https://u:p@github.com/x", "file:///etc/passwd", "https://github.com:444/x", "https://github.com:bad/x"])
def test_download_host_and_redirect_boundaries(url):
    assert not protocol.allowed_url(url)
    with pytest.raises(protocol.UpdateError):
        protocol.Redirects().redirect_request(None, None, 302, "", {}, url)


def test_network_response_size_is_bounded(monkeypatch):
    response = io.BytesIO(b"x" * 40)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(protocol.urllib.request, "build_opener", lambda *args: opener)
    with pytest.raises(protocol.UpdateError, match="size limit"):
        protocol.fetch(protocol.API, 20)


def test_status_never_contacts_github_and_is_admin_only(client, admin, agent, requester, monkeypatch):
    latest = Mock(side_effect=AssertionError("unexpected network"))
    monkeypatch.setattr(protocol, "latest", latest)
    for user in (agent, requester):
        h = as_user(user)
        assert client.get("/api/system/updates", headers=h).status_code == 403
        assert client.post("/api/system/updates/check", headers=h).status_code == 403
        assert client.post("/api/system/updates/apply", headers=h, json={"version": "1.2.0", "manifest_sha256": "a" * 64, "confirm": True}).status_code == 403
    assert client.get("/api/system/updates", headers=as_user(admin)).json()["current_version"] == VERSION
    assert client.post("/api/system/updates/check", headers=as_user(admin)).status_code == 409
    latest.assert_not_called()


def test_stable_check_cached_and_older_release_not_offered(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "updates_enabled", True)
    latest = Mock(return_value={"tag_name": "v1.0.0", "body": "Older release", "html_url": "https://evil.example"})
    monkeypatch.setattr(protocol, "latest", latest)
    signed = Mock(side_effect=AssertionError("older release must not be downloaded"))
    monkeypatch.setattr(protocol, "signed_release", signed)
    h = as_user(admin)
    result = client.post("/api/system/updates/check", headers=h).json()
    assert result["release"]["newer"] is False
    assert result["release"]["url"].startswith("https://github.com/ddziubanski/layer8/")
    assert client.post("/api/system/updates/check", headers=h).status_code == 200
    latest.assert_called_once()
    signed.assert_not_called()


def test_check_reports_network_failure_and_does_not_install(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "updates_enabled", True)
    monkeypatch.setattr(protocol, "latest", Mock(side_effect=protocol.UpdateError("Could not reach GitHub securely")))
    result = client.post("/api/system/updates/check", headers=as_user(admin)).json()
    assert result["problem"] == "Could not reach GitHub securely"
    assert result["release"] is None


def test_reviewed_update_queues_only_fixed_service_once(client, db, admin, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "updates_enabled", True)
    monkeypatch.setattr(settings, "backup_passphrase", "example backup passphrase")
    monkeypatch.setattr(updates, "ready", lambda: True)
    monkeypatch.setattr(updates, "STATE", tmp_path)
    (tmp_path / "inbox").mkdir()
    db.add(AppSetting(key=updates.CHECK_KEY, value={"release": {"version": NEXT, "url": f"https://github.com/ddziubanski/layer8/releases/tag/v{NEXT}", "notes": "Test", "newer": True, "verified": True, "manifest_sha256": "a" * 64}}))
    db.commit()
    launch = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(updates.subprocess, "run", launch)
    h = as_user(admin)
    body = {"version": NEXT, "manifest_sha256": "a" * 64, "confirm": True}
    assert client.post("/api/system/updates/apply", headers=h, json={**body, "manifest_sha256": "b" * 64}).status_code == 409
    assert client.post("/api/system/updates/apply", headers=h, json={**body, "confirm": False}).status_code == 422
    assert client.post("/api/system/updates/apply", headers=h, json={**body, "version": "1.0.0"}).status_code == 409
    assert client.post("/api/system/updates/apply", headers=h, json={**body, "url": "https://evil.example"}).status_code == 422
    assert client.post("/api/system/updates/apply", headers=h, json=body).status_code == 202
    queued = json.loads((tmp_path / "inbox/request.json").read_text())
    assert set(queued) == {"version", "manifest_sha256", "requested_by", "id"}
    assert queued["requested_by"] == admin.id and queued["version"] == f"v{NEXT}"
    assert client.post("/api/system/updates/apply", headers=h, json=body).status_code == 409
    launch.assert_called_once_with(["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", "layer8-update.service"], capture_output=True, timeout=10)


def test_progress_capability_is_bounded_and_has_no_private_status(client, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "STATE", tmp_path)
    (tmp_path / "progress").mkdir()
    token = "a" * 32
    (tmp_path / "progress" / (token + ".json")).write_text(json.dumps({"phase": "migrating", "checks": []}))
    r = client.get(f"/api/update-progress/{token}.json")
    assert r.status_code == 200
    assert r.json() == {"phase": "migrating", "checks": []}
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/update-progress/status.json").status_code == 404
    assert client.get("/api/update-progress/" + "b" * 32 + ".json").status_code == 404


def test_backup_cli_loads_stored_passphrase(db, session_factory, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import cli, configstore
    from app.backup import archive
    configstore.update(db, "backup", {"backup_passphrase": "example backup passphrase"}, None)
    db.commit()
    monkeypatch.setattr(settings, "backup_passphrase", "")
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    made = SimpleNamespace(path=tmp_path / "backup.hdbackup", size=1024)
    create = Mock(return_value=made)
    monkeypatch.setattr(archive, "create", create)
    cli.backup_command(SimpleNamespace(action="create", to=str(tmp_path), passphrase_file=None))
    assert create.call_args.kwargs["passphrase"] == "example backup passphrase"
