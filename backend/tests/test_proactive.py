# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proactive tickets: one per condition before it lapses; Verify once fixed; never twice while open."""
from datetime import timedelta

from app import proactive, sla
from app.config import settings
from app.models import Device, ProactiveAlert, Queue, Runner, Ticket, TicketCheck, TicketStatus, Vendor
from tests.conftest import make_user


def on(monkeypatch):
    monkeypatch.setattr(settings, "proactive_tickets", True)


def test_off_by_default_opens_nothing(db):
    db.add_all([Queue(name="Desk", is_default=True), Device(name="WS-0142", warranty_ends=sla.utcnow().date() + timedelta(days=5))])
    db.commit()
    assert proactive.run(db) == {"opened": 0, "cleared": 0}


def test_warranty_ticket_once_then_verify_when_renewed(db, monkeypatch):
    on(monkeypatch)
    db.add(Queue(name="Desk", is_default=True))
    soon = sla.utcnow().date() + timedelta(days=10)
    pc = Device(name="WS-0142", warranty_ends=soon)
    far = Device(name="WS-0200", warranty_ends=soon + timedelta(days=400))
    db.add_all([pc, far])
    db.commit()
    assert proactive.run(db)["opened"] == 1
    db.commit()
    t = db.query(Ticket).one()
    assert t.subject == "Warranty ending: WS-0142" and t.status not in (TicketStatus.resolved, TicketStatus.closed)
    check = db.query(TicketCheck).one()
    assert (check.kind, check.state) == ("proactive_cleared", "waiting")
    # Not again while it's open; still applies after the date passes, until renewed.
    assert proactive.run(db)["opened"] == 0
    pc.warranty_ends = soon + timedelta(days=3 * 365)
    db.commit()
    assert proactive.run(db)["cleared"] == 1
    db.commit()
    assert db.get(TicketCheck, check.id).state == "cleared"
    assert db.get(Ticket, t.id).status == t.status  # never closes by itself


def test_contracts_service_accounts_runners_and_backups(db, monkeypatch):
    on(monkeypatch)
    monkeypatch.setattr(settings, "backup_dir", "/srv/backups")
    db.add(Queue(name="Desk", is_default=True))
    now = sla.utcnow()
    db.add(Vendor(name="Contoso Networks", contract_ends=now.date() + timedelta(days=20)))
    svc = make_user(db, "svc-backup@corp.example", name="Backup service")
    person = make_user(db, "lee@corp.example", name="R. Lee")
    for u, sam in ((svc, "svc-backup"), (person, "rlee")):
        u.directory_id, u.sam_account_name, u.account_expires_at = f"g-{sam}", sam, now + timedelta(days=3)
    db.add(Runner(name="WIN-RUNNER", token_hash="x" * 64, last_seen_at=now - timedelta(hours=5), last_host="win-01"))
    from app.models import WorkerJob
    db.add(WorkerJob(name="backup", last_finished_at=now, last_ok=False, last_success_at=now - timedelta(days=4), last_error="share offline"))
    db.commit()
    proactive.run(db)
    db.commit()
    subjects = sorted(t.subject for t in db.query(Ticket))
    assert subjects == ["Backups failing", "Contract ending: Contoso Networks", "Runner not checking in: WIN-RUNNER", "Service account expiring: svc-backup"]
    # The runner checks in again: its ticket shows Verify.
    db.query(Runner).one().last_seen_at = sla.utcnow()
    db.commit()
    assert proactive.run(db)["cleared"] == 1
    db.commit()
    runner_ticket = db.get(ProactiveAlert, f"runner:{db.query(Runner).one().id}").ticket_id
    assert db.query(TicketCheck).filter_by(ticket_id=runner_ticket).one().state == "cleared"
