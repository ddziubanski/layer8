# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backups: encryption, create / verify / prune, restore into another database, and Settings > Backup."""

import io
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, make_url, text

from app import jobs
from app.backup import archive, crypto
from app.config import settings
from app.models import Queue, Ticket, TicketSource
from tests.conftest import TEST_URL, as_user

PASS = "correct horse battery staple"


@pytest.fixture
def setup(db, tmp_path, monkeypatch, requester):
    folder = tmp_path / "share"
    folder.mkdir()
    monkeypatch.setattr(settings, "backup_dir", str(folder))
    monkeypatch.setattr(settings, "backup_passphrase", PASS)
    monkeypatch.setattr(settings, "database_url", TEST_URL)
    (settings.storage_dir / "2026" / "10").mkdir(parents=True)
    (settings.storage_dir / "2026" / "10" / "abc123").write_bytes(b"an attachment")
    settings.secrets_dir.mkdir(parents=True)
    (settings.secrets_dir / "graph_client_secret").write_text("s3cret")
    (settings.secrets_dir / "graph_client_secret.new").write_text("in-flight")
    (settings.secrets_dir / "entra-pending-abc").write_text("{}")
    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.flush()
    db.add(Ticket(subject="Backed up", source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id))
    db.commit()
    return folder


def test_encryption_detects_wrong_passphrase_and_damage():
    data = os.urandom(crypto.CHUNK + 10)
    out = io.BytesIO()
    w = crypto.EncryptingWriter(out, PASS, log2_n=10)
    w.write(data)
    w.close()
    enc = out.getvalue()
    assert data[:100] not in enc
    assert io.BufferedReader(crypto.DecryptingReader(io.BytesIO(enc), PASS)).read() == data
    for bad, why in [
        (enc, "Wrong passphrase"),
        (enc[:-1], "cut short"),
        (enc[: len(enc) // 2], "cut short"),
        (enc + b"x", "unexpected data"),
        (enc[:200] + bytes([enc[200] ^ 1]) + enc[201:], "Wrong passphrase, or the backup is damaged"),
        (b"PK\x03\x04 a zip", "Not a backup file"),
    ]:
        with pytest.raises(crypto.BackupCryptoError, match=why):
            io.BufferedReader(crypto.DecryptingReader(io.BytesIO(bad), "wrong" if bad is enc else PASS)).read()


def test_create_verify_list_and_prune(db, setup):
    made = archive.create(db, now=datetime(2026, 10, 1, 2, 0).astimezone())
    assert made.name == "helpdesk-backup-20261001-020000.hdbackup" and made.path.parent == setup
    raw = made.path.read_bytes()
    assert b"s3cret" not in raw and b"an attachment" not in raw and b"Backed up" not in raw
    m = archive.verify(made.path)
    assert m.data["counts"]["tickets"] == 1
    assert {"database.dump", "storage/2026/10/abc123", "secrets/graph_client_secret"} <= set(m.data["files"])
    with pytest.raises(archive.BackupError, match="Wrong passphrase"):
        archive.verify(made.path, "not it at all")
    damaged = setup / "helpdesk-backup-damaged.hdbackup"
    damaged.write_bytes(raw[: len(raw) - 40])
    with pytest.raises(archive.BackupError, match="cut short"):
        archive.verify(damaged)
    damaged.unlink()

    for day in (2, 3):
        archive.create(db, now=datetime(2026, 10, day, 2, 0).astimezone())
    assert [b.name[16:24] for b in archive.list_backups()] == ["20261003", "20261002", "20261001"]
    assert archive.prune(setup, 2) == ["helpdesk-backup-20261001-020000.hdbackup"]
    assert len(archive.list_backups()) == 2


def test_backup_needs_folder_and_passphrase(db, setup, monkeypatch):
    monkeypatch.setattr(settings, "backup_passphrase", "")
    with pytest.raises(archive.BackupError, match="passphrase"):
        archive.create(db)
    monkeypatch.setattr(settings, "backup_dir", str(setup / "not-mounted"))
    with pytest.raises(archive.BackupError, match="doesn't exist or isn't mounted"):
        archive.create(db)


@pytest.fixture
def scratch_db():
    name = f"{make_url(TEST_URL).database or 'helpdesk_test'}_restore"
    url = make_url(TEST_URL).set(database=name)
    admin = create_engine(make_url(TEST_URL).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    yield url.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


def test_restore_into_a_fresh_install(db, setup, scratch_db):
    made = archive.create(db)
    # The new server: an empty database, empty folders (and an old file that must make way).
    (settings.storage_dir / "2026" / "10" / "abc123").unlink()
    (settings.storage_dir / "stray").write_text("from before")
    (settings.secrets_dir / "graph_client_secret").unlink()

    manifest, done = archive.restore(made.path, PASS, database_url=scratch_db)
    assert manifest.data["counts"]["tickets"] == 1
    assert done[0].startswith("Database restored")
    eng = create_engine(scratch_db)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT subject FROM tickets")).scalars().all() == ["Backed up"]
        # Grants to the restricted rules login come back too.
        assert conn.execute(text("SELECT has_table_privilege('helpdesk_rules', 'tickets', 'SELECT')")).scalar()
    eng.dispose()
    assert (settings.storage_dir / "2026" / "10" / "abc123").read_bytes() == b"an attachment"
    assert not (settings.storage_dir / "stray").exists()
    kept = list(settings.storage_dir.parent.glob(f"{settings.storage_dir.name}.before-restore-*"))
    assert len(kept) == 1 and (kept[0] / "stray").read_text() == "from before"
    secret = settings.secrets_dir / "graph_client_secret"
    assert secret.read_text() == "s3cret" and secret.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fail_restore", [False, True])
def test_updater_rollback_removes_new_foreign_keys_atomically(db, setup, scratch_db, monkeypatch, fail_restore):
    made = archive.create(db)
    eng = create_engine(scratch_db)
    with eng.begin() as connection:
        connection.execute(text("CREATE TABLE tickets (id integer PRIMARY KEY)"))
        connection.execute(text("INSERT INTO tickets VALUES (99)"))
        connection.execute(text("CREATE TABLE added_by_failed_upgrade (id integer REFERENCES tickets(id))"))
        connection.execute(text("INSERT INTO added_by_failed_upgrade VALUES (99)"))
    original_run = archive._run

    def run(command, env, what):
        if fail_restore and command[0] == "psql":
            sql = next(arg.removeprefix("--file=") for arg in command if arg.startswith("--file="))
            with open(sql, "a", encoding="utf-8") as output:
                output.write("\nSELECT layer8_intentionally_missing_function();\n")
        return original_run(command, env, what)

    monkeypatch.setattr(archive, "_run", run)
    if fail_restore:
        with pytest.raises(archive.BackupError, match="Atomic database rollback"):
            archive.restore(made.path, PASS, database_url=scratch_db, replace_public_schema=True)
    else:
        archive.restore(made.path, PASS, database_url=scratch_db, replace_public_schema=True)
    with eng.connect() as connection:
        if fail_restore:
            # Even the schema reset was rolled back: pre-attempt data/FKs remain.
            assert connection.execute(text("SELECT id FROM tickets")).scalar() == 99
            assert connection.execute(text("SELECT id FROM added_by_failed_upgrade")).scalar() == 99
        else:
            assert connection.execute(text("SELECT subject FROM tickets")).scalar() == "Backed up"
            assert connection.execute(text("SELECT to_regclass('public.added_by_failed_upgrade')")).scalar() is None
            assert connection.execute(text("SELECT has_table_privilege('helpdesk_rules', 'tickets', 'SELECT')")).scalar()
    eng.dispose()


def test_backup_job_runs_daily_at_the_set_time(db, setup, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "backup_time", "02:30")
    now = datetime(2026, 10, 19, 1, 0).astimezone()
    assert jobs.until_backup(now) == timedelta(hours=1, minutes=30)
    assert jobs.until_backup(now.replace(hour=3)) == timedelta(hours=23, minutes=30)
    job = next(j for j in jobs.build_jobs(session_factory, mail=None) if j.name == "backup")
    summary, notable = job.run(True)
    assert "written and verified" in summary and notable
    monkeypatch.setattr(settings, "backup_dir", "")
    assert job.run(False) == ("Off: no backup folder", False)


def test_settings_backup_tab(client, db, admin, agent, setup):
    r = client.get("/api/settings/backup/files", headers=as_user(admin))
    assert r.status_code == 200 and r.json() == {"folder": str(setup), "files": [], "problem": None}
    made = archive.create(db)
    files = client.get("/api/settings/backup/files", headers=as_user(admin)).json()["files"]
    assert [f["name"] for f in files] == [made.name]
    r = client.post(f"/api/settings/backup/files/{made.name}/verify", headers=as_user(admin))
    assert r.status_code == 200 and r.json()["counts"]["tickets"] == 1
    assert client.post("/api/settings/backup/files/nope.hdbackup/verify", headers=as_user(admin)).status_code == 404
    assert client.get("/api/settings/backup/files", headers=as_user(agent)).status_code == 403

    def save(**values):
        return client.put("/api/settings/backup", json={"values": values}, headers=as_user(admin))

    assert save(backup_time="25:00").status_code == 422
    assert save(backup_dir="backups").status_code == 422
    assert save(backup_passphrase="short").status_code == 422
    r = save(backup_time="03:15", backup_keep=7)
    assert r.status_code == 200, r.text
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "backup_dir", "")
        assert "Backups aren't set up" in client.get("/api/settings/backup/files", headers=as_user(admin)).json()["problem"]


# Export and import between installs


@pytest.fixture
def fresh_install(scratch_db):
    """A new install: the schema from the migrations, with their starting data."""
    from alembic import command

    from tests.conftest import alembic_config

    cfg = alembic_config()
    cfg.set_main_option("sqlalchemy.url", scratch_db)
    command.upgrade(cfg, "head")
    eng = create_engine(scratch_db)
    yield eng
    eng.dispose()


def _revisions():
    from app.cli import _known_revisions

    return _known_revisions()


def test_export_then_import_into_a_new_install(db, setup, fresh_install, tmp_path, admin):
    from sqlalchemy.orm import Session

    from app.backup import transfer
    from app.models import OutboundEmail

    desk = db.query(Queue).one()
    tier2 = Queue(name="Tier 2")
    db.add(tier2)
    db.flush()
    desk.escalation_queue_id = tier2.id  # refers to a later row of the same table
    db.add(OutboundEmail(to_address="ral@corp.local", subject="Waiting", body="x"))
    db.commit()

    out = tmp_path / "move.hdexport"
    m = transfer.export(db, out, PASS)
    assert m.data["kind"] == "export" and m.data["tables"]["tickets"]["rows"] == 1
    assert "user_sessions" not in m.data["tables"] and not any(f.startswith("secrets/") for f in m.data["files"])
    with pytest.raises(archive.BackupError, match="This is an export, not a backup"):
        archive.verify(out)

    # The new version dropped a column and added one with a default.
    with fresh_install.begin() as conn:
        conn.execute(text("ALTER TABLE tickets DROP COLUMN contact_phone"))
        conn.execute(text("ALTER TABLE tickets ADD COLUMN new_thing text NOT NULL DEFAULT 'new'"))

    # Point the file store at an empty folder, as on the new server.
    (settings.storage_dir / "2026" / "10" / "abc123").unlink()
    with Session(fresh_install) as s:
        dry = transfer.import_(s, out, PASS, dry_run=True, known_revisions=_revisions())
        s.rollback()
        assert dry.tables["tickets"] == 1 and dry.files == 1
        assert any("left out contact_phone" in n for n in dry.notes)
        assert not (settings.storage_dir / "2026" / "10" / "abc123").exists()
        assert s.execute(text("SELECT count(*) FROM tickets")).scalar() == 0

        result = transfer.import_(s, out, PASS, dry_run=False, known_revisions=_revisions())
        s.commit()
        assert any("marked as not sent" in n for n in result.notes)
        assert s.execute(text("SELECT subject, new_thing FROM tickets")).one() == ("Backed up", "new")
        assert s.execute(text("SELECT q.name FROM queues q JOIN queues e ON e.id = q.escalation_queue_id")).scalars().all() == ["Desk"]
        assert s.execute(text("SELECT status, last_error FROM outbound_emails")).one() == ("failed", "Not sent: imported from another install")
        # New rows carry on after the imported ones.
        new_id = s.execute(text("INSERT INTO queues (name) VALUES ('After') RETURNING id")).scalar()
        assert new_id > tier2.id
        s.rollback()
        assert (settings.storage_dir / "2026" / "10" / "abc123").read_bytes() == b"an attachment"

        with pytest.raises(archive.BackupError, match="already has tickets"):
            transfer.import_(s, out, PASS, dry_run=True, known_revisions=_revisions())
        s.rollback()
        with pytest.raises(archive.BackupError, match="newer version"):
            transfer.import_(s, out, PASS, dry_run=True, known_revisions=set())
