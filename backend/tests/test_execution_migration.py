"""Upgrade real pre-hardening data, including sealed work and ticket audit history."""
import uuid

from alembic import command
from sqlalchemy import create_engine, make_url, text

from tests.conftest import TEST_URL, alembic_config


def test_execution_upgrade_preserves_history_and_clears_pending_secrets(engine):
    name = "helpdesk_upgrade_" + uuid.uuid4().hex
    server = create_engine(make_url(TEST_URL).set(database="postgres"), isolation_level="AUTOCOMMIT")
    upgraded = None
    try:
        with server.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = make_url(TEST_URL).set(database=name)
        cfg = alembic_config()
        cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
        command.upgrade(cfg, "8cbdccc1f8f8")
        upgraded = create_engine(url)
        with upgraded.begin() as conn:
            uid = conn.scalar(text("INSERT INTO users (email, display_name, role) VALUES ('upgrade@example.com', 'Upgrade admin', 'admin') RETURNING id"))
            qid = conn.scalar(text("INSERT INTO queues (name) VALUES ('Upgrade test') RETURNING id"))
            tid = conn.scalar(text("INSERT INTO tickets (subject, description, status, priority, source, requester_id, queue_id) VALUES ('Upgrade audit', '', 'open', 'medium', 'agent', :u, :q) RETURNING id"), {"u": uid, "q": qid})
            sid = conn.scalar(text("INSERT INTO scripts (name, kind, language, current_version) VALUES ('Upgrade action', 'action', 'bash', 1) RETURNING id"))
            vid = conn.scalar(text("INSERT INTO script_versions (script_id, number, body, author_id) VALUES (:s, 1, 'echo example', :u) RETURNING id"), {"s": sid, "u": uid})
            for status in ("queued", "awaiting_approval", "succeeded"):
                conn.execute(text("INSERT INTO script_runs (script_id, version_id, requested_by_id, ticket_id, status, sealed) VALUES (:s, :v, :u, :t, :status, :sealed)"), {"s": sid, "v": vid, "u": uid, "t": tid, "status": status, "sealed": b"synthetic sealed value" if status != "succeeded" else None})
            # A leaver's disable booked for next week: built in, so the upgrade keeps it.
            bid = conn.scalar(text("INSERT INTO scripts (name, kind, language, current_version) VALUES ('Layer 8: disable an AD account', 'action', 'powershell', 1) RETURNING id"))
            bvid = conn.scalar(text("INSERT INTO script_versions (script_id, number, body) VALUES (:s, 1, 'Disable-ADAccount') RETURNING id"), {"s": bid})
            conn.execute(text("INSERT INTO script_runs (script_id, version_id, requested_by_id, ticket_id, status, not_before) VALUES (:s, :v, :u, :t, 'queued', now() + interval '7 days')"), {"s": bid, "v": bvid, "u": uid, "t": tid})
        command.upgrade(cfg, "a18d2040b001")
        with upgraded.connect() as conn:
            version = conn.execute(text("SELECT language, reviewed_by_id, reviewed_at FROM script_versions WHERE id = :v"), {"v": vid}).one()
            assert version == ("bash", None, None)
            rows = conn.execute(text("SELECT status, sealed, finished_at FROM script_runs WHERE script_id = :s ORDER BY id"), {"s": sid}).all()
            assert [r.status for r in rows] == ["cancelled", "cancelled", "succeeded"]
            assert all(r.sealed is None and r.finished_at is not None for r in rows[:2])
            assert conn.scalar(text("SELECT status FROM script_runs WHERE script_id = :s"), {"s": bid}) == "queued"
            assert conn.scalar(text("SELECT language FROM script_versions WHERE id = :v"), {"v": bvid}) == "powershell"
            assert conn.scalar(text("SELECT count(*) FROM comments WHERE ticket_id = :t AND is_internal"), {"t": tid}) == 2
            assert conn.scalar(text("SELECT count(*) FROM ticket_events WHERE ticket_id = :t AND type = 'comment_added'"), {"t": tid}) == 2
        # Exercise the downgrade too; it must leave runs and audit history intact.
        command.downgrade(cfg, "8cbdccc1f8f8")
        command.upgrade(cfg, "a18d2040b001")
        with upgraded.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM comments WHERE ticket_id = :t"), {"t": tid}) == 2
    finally:
        if upgraded is not None:
            upgraded.dispose()
        with server.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        server.dispose()
