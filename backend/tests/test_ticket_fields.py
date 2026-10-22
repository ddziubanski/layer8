"""Settings > Ticket fields: resolution types, closure reasons, departments, contact phone."""

import pytest

from app import configstore
from app.config import settings
from app.models import FieldList, FieldOption, UserRole
from tests.conftest import ANSWERS, as_user, make_user


@pytest.fixture
def lists(db):
    def add(lst, *labels, key=None):
        out = []
        for i, label in enumerate(labels):
            opt = FieldOption(list=lst, label=label, position=i * 10, key=key if i == len(labels) - 1 else None)
            db.add(opt)
            out.append(opt)
        return out

    fixed, workaround = add(FieldList.resolution_type, "Fixed", "Workaround")
    resolved, by_requester = add(FieldList.closure_reason, "Resolved", "Resolved by requester", key="requester_resolved")
    (finance,) = add(FieldList.department, "Finance")
    db.commit()
    return {"fixed": fixed.id, "workaround": workaround.id, "resolved": resolved.id, "by_requester": by_requester.id, "finance": finance.id}


@pytest.fixture
def ral(db):
    user = make_user(db, "ralvarez@corp.local", name="R. Alvarez")
    user.phone, user.department = "+1 555 0100", "finance"  # as synced from AD
    db.commit()
    return user


def new_ticket(client, agent, **body):
    r = client.post("/api/tickets", json={"subject": "Printer jam", **body}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    return r.json()


def test_contact_details_start_from_ad(client, db, agent, ral, lists, service_desk, slas):
    t = new_ticket(client, agent, requester_id=ral.id)
    assert t["requester"]["id"] == ral.id
    assert t["contact_phone"] == "+1 555 0100"
    assert t["department"] == {"id": lists["finance"], "label": "Finance"}  # matched case-insensitively
    # Typed in on the ticket: those win.
    t = new_ticket(client, agent, requester_id=ral.id, contact_phone="ext. 4410", department_id=None)
    assert t["contact_phone"] == "ext. 4410"


def test_new_ad_departments_are_added_unless_turned_off(client, db, admin, agent, service_desk, slas):
    hr = make_user(db, "hr@corp.local", name="H. R.")
    hr.department = "Human Resources"
    db.commit()
    assert new_ticket(client, agent, requester_id=hr.id)["department"]["label"] == "Human Resources"
    labels = [o["label"] for o in client.get("/api/field-options", params={"list": "department"}, headers=as_user(agent)).json()]
    assert labels == ["Human Resources"]

    configstore.update(db, "ticket_fields", {"departments_from_ad": False}, admin)
    db.commit()
    hr.department = "People Ops"
    db.commit()
    assert new_ticket(client, agent, requester_id=hr.id)["department"] is None


def test_resolving_needs_resolution_type_and_closure_reason(client, db, agent, ral, lists, service_desk, slas):
    t = new_ticket(client, agent, requester_id=ral.id)
    r = client.patch(f"/api/tickets/{t['id']}", json={"status": "resolved"}, headers=as_user(agent))
    assert r.status_code == 409 and r.json()["detail"] == "Choose a resolution type and a closure reason to resolve or close this ticket"
    r = client.patch(f"/api/tickets/{t['id']}", json={"status": "resolved", "resolution_type_id": lists["fixed"], "closure_reason_id": lists["resolved"]}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["resolution_type"]["label"], r.json()["closure_reason"]["label"]) == ("resolved", "Fixed", "Resolved")
    events = client.get(f"/api/tickets/{t['id']}/events", headers=as_user(agent)).json()
    updated = next(e for e in events if e["type"] == "updated")
    assert updated["data"]["fields"]["resolution_type"] == {"before": None, "after": "Fixed"}

    # Reopened: asked again next time.
    r = client.patch(f"/api/tickets/{t['id']}", json={"status": "open"}, headers=as_user(agent)).json()
    assert r["resolution_type"] is None and r["closure_reason"] is None


def test_required_can_be_turned_off(client, db, admin, agent, ral, lists, service_desk, slas):
    configstore.update(db, "ticket_fields", {"require_resolution_type": False, "require_closure_reason": False}, admin)
    db.commit()
    t = new_ticket(client, agent, requester_id=ral.id)
    assert client.patch(f"/api/tickets/{t['id']}", json={"status": "closed"}, headers=as_user(agent)).status_code == 200


def test_choices_must_be_active_and_of_the_right_list(client, db, admin, agent, ral, lists, service_desk, slas):
    t = new_ticket(client, agent, requester_id=ral.id)
    r = client.patch(f"/api/tickets/{t['id']}", json={"resolution_type_id": lists["resolved"]}, headers=as_user(agent))
    assert r.status_code == 409 and "resolution type" in r.json()["detail"]
    client.patch(f"/api/tickets/{t['id']}", json={"department_id": lists["finance"]}, headers=as_user(agent))
    # Retiring a choice keeps it on tickets that have it, but it can't be picked again.
    client.patch(f"/api/field-options/{lists['finance']}", json={"is_active": False}, headers=as_user(admin))
    assert client.get(f"/api/tickets/{t['id']}", headers=as_user(agent)).json()["department"]["label"] == "Finance"
    # Saving the ticket form with its current (retired) department is fine...
    assert client.patch(f"/api/tickets/{t['id']}", json={"department_id": lists["finance"]}, headers=as_user(agent)).status_code == 200
    # ...but no other ticket can be given it.
    other = new_ticket(client, agent, requester_id=ral.id)
    r = client.patch(f"/api/tickets/{other['id']}", json={"department_id": lists["finance"]}, headers=as_user(agent))
    assert r.status_code == 409


def test_changing_requester_moves_their_contact_details(client, db, agent, ral, lists, service_desk, slas):
    mos = make_user(db, "mosei@corp.local", name="M. Osei")
    mos.phone = "+1 555 0199"
    db.commit()
    t = new_ticket(client, agent, requester_id=ral.id)
    r = client.post(f"/api/tickets/{t['id']}/requester", json={"user_id": mos.id}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert (r.json()["requester"]["id"], r.json()["contact_phone"]) == (mos.id, "+1 555 0199")
    # A phone typed in by the agent stays when the requester changes back.
    client.patch(f"/api/tickets/{t['id']}", json={"contact_phone": "mobile 07700 900123"}, headers=as_user(agent))
    r = client.post(f"/api/tickets/{t['id']}/requester", json={"user_id": ral.id}, headers=as_user(agent))
    assert r.json()["contact_phone"] == "mobile 07700 900123"


def test_portal_self_close_is_recorded(client, db, ral, lists, service_desk, slas):
    r = client.post("/api/portal/tickets", json={"subject": "Printer jam", "description": "x", **ANSWERS}, headers=as_user(ral))
    tid = r.json()["id"]
    assert client.post(f"/api/portal/tickets/{tid}/resolve", headers=as_user(ral)).status_code == 200
    from app.models import Ticket

    db.expire_all()
    assert db.get(Ticket, tid).closure_reason.label == "Resolved by requester"


def test_admins_edit_the_lists(client, db, admin, agent, ral, lists):
    r = client.post("/api/field-options", json={"list": "resolution_type", "label": "Advice given"}, headers=as_user(admin))
    assert r.status_code == 201
    new_id = r.json()["id"]
    assert client.post("/api/field-options", json={"list": "resolution_type", "label": "fixed"}, headers=as_user(admin)).status_code == 409
    assert client.post("/api/field-options", json={"list": "resolution_type", "label": "X"}, headers=as_user(agent)).status_code == 403
    client.patch(f"/api/field-options/{new_id}", json={"label": "Advice or how-to"}, headers=as_user(admin))
    order = [new_id, lists["workaround"], lists["fixed"]]
    r = client.put("/api/field-options/order/resolution_type", json=order, headers=as_user(admin))
    assert [o["label"] for o in r.json()] == ["Advice or how-to", "Workaround", "Fixed"]
    assert client.put("/api/field-options/order/resolution_type", json=order[:2], headers=as_user(admin)).status_code == 422

    make_user(db, "x@corp.local", name="X").department = "IT"
    db.commit()
    r = client.post("/api/field-options/departments/import", headers=as_user(admin))
    # R. Alvarez's "finance" matches Finance; IT is new.
    assert [o["label"] for o in r.json()] == ["Finance", "IT"]


def test_requester_picker_search(client, db, agent, ral):
    make_user(db, "rachel@corp.local", name="Rachel 100% Green")
    gone = make_user(db, "rold@corp.local", name="R. Old")
    gone.is_active = False
    db.commit()
    names = lambda q: [u["display_name"] for u in client.get("/api/users", params={"q": q, "limit": 20}, headers=as_user(agent)).json()]  # noqa: E731
    assert names("r.") == ["R. Alvarez"]  # inactive people aren't offered
    assert names("ralvarez@") == ["R. Alvarez"]
    assert names("100%") == ["Rachel 100% Green"]  # % is just a character
