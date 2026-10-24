# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings: Windows sign-in (keytab upload, on/off), admins only."""

import base64
import os
import ssl
import stat
import struct
from pathlib import Path

import pytest

from app import cli, configstore, kerberos
from app.config import settings
from tests.conftest import as_user

AES256, RC4 = 18, 23


def keytab(*entries: tuple[str, int]) -> bytes:
    """A keytab file (MIT format 0x0502) with made-up keys: ("HTTP/host@REALM", enctype)."""

    def counted(b: bytes) -> bytes:
        return struct.pack(">H", len(b)) + b

    out = b"\x05\x02"
    for principal, enctype in entries:
        name, realm = principal.rsplit("@", 1)
        parts = name.split("/")
        rec = struct.pack(">H", len(parts)) + counted(realm.encode()) + b"".join(counted(p.encode()) for p in parts)
        rec += struct.pack(">IIBH", 1, 0, 3, enctype) + counted(os.urandom(32)) + struct.pack(">I", 3)
        out += struct.pack(">i", len(rec)) + rec
    return out


GOOD = keytab(("HTTP/helpdesk.corp.example@CORP.EXAMPLE", AES256), ("HTTP/helpdesk.corp.example@CORP.EXAMPLE", 17))


def upload(client, admin, data, name="http.keytab"):
    return client.put("/api/settings/windows-sign-in/keytab", files={"file": (name, data, "application/octet-stream")}, headers=as_user(admin))


def test_upload_keytab_then_turn_on(client, admin):
    r = client.get("/api/settings/windows-sign-in", headers=as_user(admin))
    assert r.json()["enabled"] is False and r.json()["keytab_principals"] == []
    # Not before there's a keytab.
    r = client.put("/api/settings/windows-sign-in", json={"enabled": True, "silent": True}, headers=as_user(admin))
    assert r.status_code == 422 and "keytab" in r.json()["detail"]

    r = upload(client, admin, GOOD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["realm"] == "CORP.EXAMPLE"
    assert body["keytab_principals"] == ["HTTP/helpdesk.corp.example@CORP.EXAMPLE"]
    assert body["keytab_enctypes"] == ["aes128-cts-hmac-sha1-96", "aes256-cts-hmac-sha1-96"]
    assert body["keytab_uploaded_by"] == admin.display_name and body["problem"] is None
    # Stored for this service only, never in the database.
    path = kerberos.keytab_path()
    assert path.read_bytes() == GOOD
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert client.get("/api/auth/options").json()["windows_sign_in"] is False
    r = client.put("/api/settings/windows-sign-in", json={"enabled": True, "silent": False}, headers=as_user(admin))
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert client.get("/api/auth/options").json() == {"windows_sign_in": True, "windows_sign_in_silent": False}


def test_bad_keytabs_are_refused_with_a_reason(client, admin):
    cases = {
        b"not a keytab": "isn't a keytab",
        GOOD[:-10]: "cut short",
        keytab(("HTTP/helpdesk.corp.example@CORP.EXAMPLE", RC4)): "RC4",
        keytab(("host/pc01.corp.example@CORP.EXAMPLE", AES256)): "no HTTP/",
        keytab(("HTTP/a.corp.example@CORP.EXAMPLE", AES256), ("HTTP/a.corp.local@CORP.LOCAL", AES256)): "mixes realms",
        b"\x05\x02" + bytes(70_000): "too big",
    }
    for data, why in cases.items():
        r = upload(client, admin, data)
        assert r.status_code == 422, why
        assert why in r.json()["detail"], r.json()
    assert not kerberos.keytab_path().exists()


def test_removing_the_keytab_turns_it_off(client, admin):
    upload(client, admin, GOOD)
    client.put("/api/settings/windows-sign-in", json={"enabled": True, "silent": True}, headers=as_user(admin))
    r = client.delete("/api/settings/windows-sign-in/keytab", headers=as_user(admin))
    assert r.status_code == 200
    assert (r.json()["enabled"], r.json()["keytab_principals"], r.json()["realm"]) == (False, [], "")
    assert not kerberos.keytab_path().exists()
    assert client.get("/api/auth/options").json()["windows_sign_in"] is False


def test_keytab_missing_from_server_is_reported(client, admin):
    upload(client, admin, GOOD)
    kerberos.keytab_path().unlink()
    r = client.get("/api/settings/windows-sign-in", headers=as_user(admin))
    assert "missing" in r.json()["problem"]


def test_admins_only(client, agent):
    assert client.get("/api/settings/windows-sign-in", headers=as_user(agent)).status_code == 403
    assert upload(client, agent, GOOD).status_code == 403


# General settings, and the generic sections behind them


def section(client, admin, name="general"):
    r = client.get(f"/api/settings/{name}", headers=as_user(admin))
    assert r.status_code == 200, r.text
    return {f["name"]: f for f in r.json()["fields"]}


def test_general_settings_take_effect(client, db, admin, service_desk, slas):
    fields = section(client, admin)
    assert fields["portal_url"]["value"] == "" and fields["portal_url"]["kind"] == "text"
    assert fields["max_upload_mb"]["kind"] == "int" and fields["max_upload_mb"]["locked"] is False

    r = client.put(
        "/api/settings/general", json={"values": {"portal_url": " https://helpdesk.corp.example ", "max_upload_mb": 1}}, headers=as_user(admin)
    )
    assert r.status_code == 200, r.text
    assert section(client, admin)["portal_url"]["value"] == "https://helpdesk.corp.example"
    assert settings.portal_url == "https://helpdesk.corp.example"
    # The upload limit is the stored one now.
    ticket = client.post("/api/tickets", json={"subject": "x"}, headers=as_user(admin)).json()
    r = client.post(f"/api/tickets/{ticket['id']}/attachments", files={"file": ("big.bin", bytes(1024 * 1024 + 1))}, headers=as_user(admin))
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]

    # Back to the default.
    client.put("/api/settings/general", json={"clear": ["max_upload_mb"]}, headers=as_user(admin))
    assert section(client, admin)["max_upload_mb"]["value"] == 25


def test_bad_values_are_refused_and_nothing_changes(client, admin):
    for values, why in [
        ({"portal_url": "helpdesk.corp.example"}, "https://"),
        ({"session_hours": 0}, "between 1 and 720"),
        ({"session_hours": "lots"}, "expected int"),
        ({"portal_url": "https://ok.corp.example", "nope": 1}, "isn't a general setting"),
    ]:
        r = client.put("/api/settings/general", json={"values": values}, headers=as_user(admin))
        assert r.status_code == 422, values
        assert why in r.json()["detail"]
    assert section(client, admin)["portal_url"]["value"] == ""
    assert client.get("/api/settings/nonsense", headers=as_user(admin)).status_code == 404


def test_environment_wins_and_locks_the_field(client, admin, monkeypatch):
    monkeypatch.setattr(configstore, "FROM_ENVIRONMENT", frozenset({"session_hours"}))
    assert section(client, admin)["session_hours"]["locked"] is True
    r = client.put("/api/settings/general", json={"values": {"session_hours": 8}}, headers=as_user(admin))
    assert r.status_code == 422 and "HELPDESK_SESSION_HOURS" in r.json()["detail"]


def test_other_processes_pick_up_changes(client, db, admin):
    client.put("/api/settings/general", json={"values": {"session_hours": 9}}, headers=as_user(admin))
    configstore.reset()  # as if this were the worker, which never saw the change
    assert settings.session_hours == 12
    configstore.refresh(db, force=True)
    assert settings.session_hours == 9


def test_installer_answers_file(db, tmp_path):
    keytab_file = tmp_path / "helpdesk.keytab"
    keytab_file.write_bytes(GOOD)
    done = cli.import_settings(
        db,
        {
            "general": {"portal_url": "https://helpdesk.corp.example", "session_hours": 10},
            "windows_sign_in": {"keytab_file": str(keytab_file), "enabled": True, "silent": False},
        },
    )
    db.commit()
    assert len(done) == 3
    assert (settings.portal_url, settings.session_hours) == ("https://helpdesk.corp.example", 10)
    config = kerberos.load(db)
    assert (config.enabled, config.silent, config.realm, config.keytab_uploaded_by) == (True, False, "CORP.EXAMPLE", "installer")


def test_installer_answers_can_point_at_files(db, tmp_path):
    (tmp_path / "bind.txt").write_text("From-a-file-1\n")
    cli.import_settings(db, {"directory": {"ldap_bind_password": {"file": "bind.txt"}}}, tmp_path)
    db.commit()
    assert settings.ldap_bind_password == "From-a-file-1"


def test_installer_answers_are_checked(db):
    for answers, why in [
        ({"general": {"portal_url": "nope"}}, "https://"),
        ({"genral": {}}, "Unknown sections: genral"),
        ({"windows_sign_in": {"enabled": True}}, "keytab_file"),
    ]:
        with pytest.raises(configstore.SettingsError, match=why):
            cli.import_settings(db, answers)
        db.rollback()


# Directory connection settings

CA_PEM = (Path(__file__).resolve().parents[2] / "dev" / "ad" / "out" / "ca.pem")


def test_directory_settings_build_the_connection(client, admin, monkeypatch):
    from app.directory import ldap, sync

    fields = section(client, admin, "directory")
    assert fields["ldap_kind"]["kind"] == "choice" and [c["value"] for c in fields["ldap_kind"]["choices"]] == ["ad", "generic"]
    assert fields["ldap_bind_password"]["kind"] == "secret" and fields["ldap_bind_password"]["is_set"] is False
    assert client.get("/api/directory/status", headers=as_user(admin)).json()["configured"] is False

    pem = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(fake_cert_der()).decode() + "\n-----END CERTIFICATE-----\n"
    r = client.put(
        "/api/settings/directory",
        json={
            "values": {
                "ldap_url": "ldaps://dc1.corp.example, ldaps://dc2.corp.example",
                "ldap_bind_dn": "CN=svc-helpdesk,OU=Service Accounts,DC=corp,DC=example",
                "ldap_bind_password": "Bind-pass-1",
                "ldap_base_dn": "DC=corp,DC=example",
                "ldap_ca_file": pem,
            }
        },
        headers=as_user(admin),
    )
    assert r.status_code == 200, r.text
    fields = {f["name"]: f for f in r.json()["fields"]}
    # The password is stored but never shown again.
    assert fields["ldap_bind_password"] == {**fields["ldap_bind_password"], "is_set": True, "value": None}
    assert "Bind-pass-1" not in r.text
    assert fields["ldap_ca_file"]["value"] == pem
    assert client.get("/api/directory/status", headers=as_user(admin)).json()["servers"] == ["ldaps://dc1.corp.example", "ldaps://dc2.corp.example"]

    directory = sync.get_directory()
    assert isinstance(directory, ldap.LdapDirectory)
    assert settings.ldap_bind_password == "Bind-pass-1"
    ca = Path(settings.ldap_ca_file)
    assert ca.read_text() == pem and ca.parent == settings.secrets_dir
    assert stat.S_IMODE((settings.secrets_dir / "ldap_bind_password").stat().st_mode) == 0o600

    # Clearing the certificate falls back to the server's trusted CAs.
    client.put("/api/settings/directory", json={"values": {"ldap_ca_file": ""}}, headers=as_user(admin))
    assert not ca.exists() and settings.ldap_ca_file == ""


def test_directory_settings_are_checked(client, admin):
    for values, why in [
        ({"ldap_url": "dc1.corp.example"}, "ldaps://dc1.corp.example"),
        ({"ldap_kind": "novell"}, "pick one of ad, generic"),
        ({"ldap_ca_file": "not a cert"}, "PEM form"),
        ({"ldap_ca_file": "-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----"}, "damaged"),
        ({"ldap_sync_minutes": 1}, "between 5 and 1440"),
    ]:
        r = client.put("/api/settings/directory", json={"values": values}, headers=as_user(admin))
        assert r.status_code == 422, values
        assert why in r.json()["detail"], r.json()


def fake_cert_der() -> bytes:
    """Any well-formed certificate will do; the dev AD's test CA if it's there."""
    if CA_PEM.exists():
        return ssl.PEM_cert_to_DER_cert(CA_PEM.read_text())
    pytest.skip("needs dev/ad/out/ca.pem (docker compose --profile ad up -d ad)")


# Email (Microsoft Graph)

TENANT, CLIENT = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-000000000000"


def test_email_settings_and_test_button(client, admin, monkeypatch):
    from app.mail import graph

    assert client.post("/api/settings/email/test", headers=as_user(admin)).status_code == 409
    r = client.put(
        "/api/settings/email",
        json={"values": {"mailbox": "helpdesk@example.com", "graph_tenant_id": TENANT, "graph_client_id": CLIENT, "mail_allowed_domains": ["example.com", "", " @corp.example "]}},
        headers=as_user(admin),
    )
    assert r.status_code == 200, r.text
    assert settings.mail_allowed_domains == ["example.com", "@corp.example"]
    r = client.post("/api/settings/email/test", headers=as_user(admin))
    assert r.status_code == 409 and r.json()["detail"] == "Still needed: client secret"

    client.put("/api/settings/email", json={"values": {"graph_client_secret": "s3cret~value"}}, headers=as_user(admin))
    seen = {}

    def fake_from_settings(s):
        seen["secret"] = s.graph_client_secret
        return type("M", (), {"check": lambda self: {"mailbox": s.mailbox, "inbox_items": 3, "unread": 1}})()

    monkeypatch.setattr(graph, "from_settings", fake_from_settings)
    r = client.post("/api/settings/email/test", headers=as_user(admin))
    assert r.status_code == 200 and r.json() == {"mailbox": "helpdesk@example.com", "inbox_items": 3, "unread": 1}
    assert seen["secret"] == "s3cret~value"

    def failing(s):
        raise RuntimeError("Graph sign-in failed: invalid_client: AADSTS7000215: Invalid client secret provided.")

    monkeypatch.setattr(graph, "from_settings", failing)
    r = client.post("/api/settings/email/test", headers=as_user(admin))
    assert r.status_code == 502 and "AADSTS7000215" in r.json()["detail"]


def test_email_settings_are_checked(client, admin):
    for values, why in [
        ({"mailbox": "helpdesk"}, "email address"),
        ({"graph_tenant_id": "example.com"}, "GUID"),
        ({"mail_allowed_domains": ["example org"]}, "isn't a domain name"),
        ({"mail_poll_seconds": 1}, "between 10 and 3600"),
    ]:
        r = client.put("/api/settings/email", json={"values": values}, headers=as_user(admin))
        assert r.status_code == 422, values
        assert why in r.json()["detail"], r.json()


def test_graph_check_reads_the_inbox():
    import httpx

    from app.mail.graph import GraphMailbox

    def handler(request):
        assert request.url.path == "/v1.0/users/helpdesk@example.com/mailFolders/inbox"
        return httpx.Response(200, json={"displayName": "Inbox", "totalItemCount": 7, "unreadItemCount": 2})

    box = GraphMailbox("helpdesk@example.com", token=lambda: "t", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert box.check() == {"mailbox": "helpdesk@example.com", "inbox_items": 7, "unread": 2}


def test_worker_follows_email_settings(db, admin, monkeypatch):
    from app import worker
    from app.mail import graph

    built = []
    monkeypatch.setattr(graph, "from_settings", lambda s: built.append(s.mailbox) or f"mailbox:{s.mailbox}")
    mail = worker.MailSetup()
    assert mail.update()[0] is None  # nothing set: off, and the worker keeps running
    configstore.update(db, "email", {"mailbox": "helpdesk@example.com", "graph_tenant_id": TENANT, "graph_client_id": CLIENT}, admin)
    assert mail.update()[0] is None  # still needs the secret
    configstore.update(db, "email", {"graph_client_secret": "s"}, admin)
    box, cfg = mail.update()
    assert box == "mailbox:helpdesk@example.com" and cfg.mailbox_address == "helpdesk@example.com"
    mail.update()
    assert built == ["helpdesk@example.com"]  # only rebuilt when the settings change
    configstore.update(db, "email", {"mailbox": "it@example.com"}, admin)
    assert mail.update()[0] == "mailbox:it@example.com"


def test_sql_rule_time_limit(client, admin):
    from app.rules import sqlmode

    r = client.put("/api/settings/rules", json={"values": {"rules_statement_timeout_ms": 2500}}, headers=as_user(admin))
    assert r.status_code == 200, r.text
    # The rules login's connections are opened with the new limit.
    with sqlmode.engine().connect() as conn:
        assert conn.exec_driver_sql("SHOW statement_timeout").scalar() == "2500ms"
    assert client.put("/api/settings/rules", json={"values": {"rules_statement_timeout_ms": 5}}, headers=as_user(admin)).status_code == 422


def test_check_mailbox_now(client, db, admin, service_desk, slas, monkeypatch, session_factory):
    from sqlalchemy import text

    from app.mail import graph, runner
    from app.models import Ticket
    from tests.test_mail_intake import FakeMailbox

    assert client.post("/api/settings/email/check-now", headers=as_user(admin)).status_code == 409
    configstore.update(
        db, "email",
        {"mailbox": FakeMailbox.address, "graph_tenant_id": TENANT, "graph_client_id": CLIENT, "graph_client_secret": "s", "mail_allowed_domains": ["corp.local"]},
        admin,
    )
    db.commit()
    box = FakeMailbox()
    monkeypatch.setattr(graph, "from_settings", lambda s: box)
    box.deliver(from_address="ralvarez@corp.local", subject="Printer jam", body_text="Again.")
    box.deliver(from_address="someone@elsewhere.com", subject="Hello", body_text="Spam?")

    r = client.post("/api/settings/email/check-now", headers=as_user(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["processed"], body["rejected"], body["failed"]) == (1, 1, 0)
    ticket_id = next(i["ticket_id"] for i in body["items"] if i["status"] == "processed")
    assert db.get(Ticket, ticket_id).subject == "Printer jam"
    # The new ticket's acknowledgement went out in the same pass.
    assert body["sent"] == 1 and len(box.sent) == 1
    assert box.inbox == []

    # While another pass holds the lock (e.g. the worker), it says so instead of racing it.
    with session_factory() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": runner.MAIL_LOCK})
        r = client.post("/api/settings/email/check-now", headers=as_user(admin))
        assert r.status_code == 409 and "right now" in r.json()["detail"]
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": runner.MAIL_LOCK})
    assert client.post("/api/settings/email/check-now", headers=as_user(admin)).status_code == 200
