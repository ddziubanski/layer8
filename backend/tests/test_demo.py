"""The offline demo's sample data, and the installer's setup command."""

import json

from app import demo
from app.models import Device, Script, ServiceNotice, Ticket, User


def test_demo_loads_and_signs_in(client, db):
    done = demo.install(db)
    db.commit()
    assert any("tickets" in d for d in done)
    assert db.query(Ticket).count() == len(demo.TICKETS)
    assert db.query(Device).count() == len(demo.DEVICES)
    assert db.query(Script).count() >= 5
    assert db.query(ServiceNotice).filter_by(title="This is a demo").count() == 1
    vpn = db.query(Ticket).filter(Ticket.subject.like("Can't connect to VPN%")).one()
    assert vpn.computer.name == "WS-0143" and vpn.assignee.email == "jpark@demo.local"
    # Again: nothing doubles.
    demo.install(db)
    db.commit()
    assert db.query(Ticket).count() == len(demo.TICKETS)
    r = client.post("/api/auth/login", json={"email": "admin@demo.local", "password": demo.DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    assert "Demo" in client.get("/api/branding").json()["sign_in_message"]


def test_setup_command_applies_settings_and_admin(db, tmp_path, monkeypatch):
    from app import cli

    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        "install": {"admin": {"email": "it@example.com", "name": "IT Admin", "password": "A-long-admin-pass-1"}},
        "general": {"portal_url": "https://helpdesk.corp.example", "ticket_label_format": "HD-{n}"},
    }))
    cli.setup(answers)
    assert db.query(User).filter_by(email="it@example.com").one().role == "admin"
