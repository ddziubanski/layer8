# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from sqlalchemy import select

from app import cli, configstore
from app.models import ChecklistTemplate, Queue, TicketTemplate
from tests.conftest import as_user

NEW = {"name": "New starter laptop", "subject": "Laptop for a new starter", "description": "Model:\nStart date:", "category": "Hardware", "priority": "high"}


@pytest.fixture
def queues(db):
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.commit()
    return desk, hr


def test_managers_edit_technicians_list(client, db, admin, agent, queues):
    desk, hr = queues
    a = as_user(admin)
    made = client.post("/api/ticket-templates", json={**NEW, "queue_id": desk.id}, headers=a)
    assert made.status_code == 201, made.text
    assert made.json()["queue_name"] == "Service desk"
    client.post("/api/ticket-templates", json={**NEW, "name": "HR only", "queue_id": hr.id}, headers=a)
    client.post("/api/ticket-templates", json={**NEW, "name": "Retired", "is_active": False}, headers=a)
    # Technicians can't edit; they're offered active templates for queues they can see.
    t = as_user(agent)
    assert client.post("/api/ticket-templates", json=NEW, headers=t).status_code == 403
    assert [x["name"] for x in client.get("/api/ticket-templates", headers=t).json()] == ["New starter laptop"]
    assert {x["name"] for x in client.get("/api/ticket-templates?all=true", headers=a).json()} == {"New starter laptop", "HR only", "Retired"}
    tid = made.json()["id"]
    assert client.put(f"/api/ticket-templates/{tid}", json={**NEW, "name": "HR only"}, headers=a).status_code == 409
    assert client.put(f"/api/ticket-templates/{tid}", json={**NEW, "priority": "low"}, headers=a).json()["priority"] == "low"
    assert client.delete(f"/api/ticket-templates/{tid}", headers=a).status_code == 204


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "   "}, {"description": "x" * 20001}, {"priority": "whenever"}, {"queue_id": 999999}, {"requester_id": 5}, {"assignee_id": 5}])
def test_validated_and_holds_no_people(client, admin, queues, bad):
    r = client.post("/api/ticket-templates", json={**NEW, **bad}, headers=as_user(admin))
    assert r.status_code == 422



def test_answers_file_import(db, queues):
    db.add(ChecklistTemplate(name="New starter", kind="onboarding", description="", items=[]))
    db.commit()
    done = cli.import_settings(db, {"ticket_templates": [{**NEW, "queue": "service desk", "checklist": "New starter"}]})
    db.commit()
    row = db.scalar(select(TicketTemplate))
    assert done == ["ticket template: New starter laptop"] and row.queue.name == "Service desk" and row.checklist_id is not None
    # Importing again updates by name.
    cli.import_settings(db, {"ticket_templates": [{**NEW, "subject": "Changed"}]})
    db.commit()
    assert db.scalar(select(TicketTemplate)).subject == "Changed" and len(db.scalars(select(TicketTemplate)).all()) == 1
    for bad in ({**NEW, "queue": "Nowhere"}, {**NEW, "assignee": "x@corp.local"}, {**NEW, "priority": "soon"}):
        with pytest.raises(configstore.SettingsError):
            cli.import_settings(db, {"ticket_templates": [bad]})
        db.rollback()


def test_inactive_queue_templates_are_not_offered(client, db, admin, agent, queues):
    desk, _ = queues
    made = client.post("/api/ticket-templates", json={**NEW, "queue_id": desk.id}, headers=as_user(admin))
    assert made.status_code == 201
    desk.is_active = False
    db.commit()
    assert client.get("/api/ticket-templates", headers=as_user(agent)).json() == []
    assert len(client.get("/api/ticket-templates?all=true", headers=as_user(admin)).json()) == 1
    assert client.post("/api/ticket-templates", json={**NEW, "name": "Inactive", "queue_id": desk.id}, headers=as_user(admin)).status_code == 422


def test_answers_file_rejects_oversized_and_inactive_queue(db, queues):
    desk, _ = queues
    desk.is_active = False
    db.commit()
    for bad in ({**NEW, "description": "x" * 20001}, {**NEW, "name": "x" * 121}, {**NEW, "queue": desk.name}):
        with pytest.raises(configstore.SettingsError):
            cli.import_settings(db, {"ticket_templates": [bad]})
        db.rollback()
