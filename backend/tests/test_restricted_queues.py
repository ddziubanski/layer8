"""Restricted queues (e.g. Human Resources): only owners, managers, viewers and admins see them."""

import pytest
from sqlalchemy import select

from app.models import Comment, Group, Queue, Ticket, UserRole
from tests.conftest import ANSWERS, as_user, make_user


@pytest.fixture
def hr(client, db, admin, service_desk):
    people = {
        "admin": admin,
        "hr_owner": make_user(db, "hr.owner@corp.local", UserRole.agent, "HR Owner"),
        "hr_lead": make_user(db, "hr.lead@corp.local", UserRole.agent, "HR Lead"),
        "auditor": make_user(db, "auditor@corp.local", UserRole.agent, "Auditor"),
        "it": make_user(db, "it.tech@corp.local", UserRole.agent, "IT Tech"),
        "staff": make_user(db, "staff@corp.local", name="Staff Member"),
    }
    queue = Queue(name="Human Resources", show_in_portal=True, restricted=True, categories=["Payroll"])
    db.add(queue)
    db.commit()
    hdr = as_user(admin)
    client.put(f"/api/queues/{queue.id}/owners", json={"user_ids": [people["hr_owner"].id]}, headers=hdr)
    client.put(f"/api/queues/{queue.id}/managers", json={"user_ids": [people["hr_lead"].id]}, headers=hdr)
    # A flat, app-made group of viewers: no AD nesting needed.
    group = client.post("/api/groups", json={"name": "HR auditors"}, headers=hdr).json()
    client.put(f"/api/groups/{group['id']}/members", json={"user_ids": [people["auditor"].id]}, headers=hdr)
    r = client.put(f"/api/queues/{queue.id}/viewers", json={"group_ids": [group["id"]]}, headers=hdr)
    assert r.status_code == 200, r.text
    tid = client.post("/api/portal/tickets", json={"queue_id": queue.id, "category": "Payroll", "subject": "Wrong pay", **ANSWERS}, headers=as_user(people["staff"])).json()["id"]
    note = client.post(f"/api/tickets/{tid}/comments", json={"body": "Salary details", "is_internal": True}, headers=as_user(people["hr_owner"])).json()
    upload = client.post(
        f"/api/tickets/{tid}/attachments", files={"file": ("payslip.pdf", b"%PDF", "application/pdf")}, data={"comment_id": str(note["id"])}, headers=as_user(people["hr_owner"])
    ).json()
    return {**people, "queue": queue, "ticket": tid, "file": upload["id"]}


def ids(client, user, **params):
    return [t["id"] for t in client.get("/api/tickets", params=params, headers=as_user(user)).json()["items"]]


def test_outsiders_see_nothing_of_a_restricted_queue(client, hr):
    it, tid = hr["it"], hr["ticket"]
    assert tid not in ids(client, it)
    assert tid not in ids(client, it, view="unassigned")
    assert client.get("/api/tickets/views", headers=as_user(it)).json()["unassigned"] == 0
    for method, path in [
        ("get", f"/api/tickets/{tid}"),
        ("get", f"/api/tickets/{tid}/comments"),
        ("get", f"/api/tickets/{tid}/attachments"),
        ("get", f"/api/tickets/{tid}/events"),
        ("get", f"/api/attachments/{hr['file']}/download"),
        ("get", f"/api/attachments/{hr['file']}/view"),
    ]:
        assert getattr(client, method)(path, headers=as_user(it)).status_code == 404, path
    assert client.patch(f"/api/tickets/{tid}", json={"status": "closed"}, headers=as_user(it)).status_code == 404
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "peek"}, headers=as_user(it)).status_code == 404
    assert client.post(f"/api/tickets/{tid}/assign", json={"assignee_id": it.id}, headers=as_user(it)).status_code == 404


@pytest.mark.parametrize("who", ["hr_owner", "hr_lead", "auditor", "admin"])
def test_owners_managers_viewers_and_admins_see_it(client, hr, who):
    user = hr[who]
    assert hr["ticket"] in ids(client, user)
    comments = client.get(f"/api/tickets/{hr['ticket']}/comments", headers=as_user(user)).json()
    assert [c["body"] for c in comments] == ["Salary details"]
    assert client.get(f"/api/attachments/{hr['file']}/download", headers=as_user(user)).status_code == 200


def test_only_people_who_can_see_it_can_be_assigned(client, hr):
    names = [u["display_name"] for u in client.get(f"/api/queues/{hr['queue'].id}/assignable", headers=as_user(hr["admin"])).json()]
    assert names == ["HR Owner"]
    r = client.post(f"/api/tickets/{hr['ticket']}/assign", json={"assignee_id": hr["it"].id}, headers=as_user(hr["admin"]))
    assert r.status_code == 409


def test_handing_a_ticket_to_hr_then_losing_sight_of_it(client, db, hr, service_desk):
    it = hr["it"]
    tid = client.post("/api/tickets", json={"subject": "Actually a payroll question"}, headers=as_user(it)).json()["id"]
    assert client.post(f"/api/tickets/{tid}/assign", json={"queue_id": hr["queue"].id, "note": "For HR"}, headers=as_user(it)).status_code == 200
    assert client.get(f"/api/tickets/{tid}", headers=as_user(it)).status_code == 404
    assert tid in ids(client, hr["hr_owner"])


def test_a_technician_filing_their_own_hr_request_is_just_the_requester(client, db, hr):
    it = hr["it"]
    tid = client.post("/api/portal/tickets", json={"queue_id": hr["queue"].id, "category": "Payroll", "subject": "My overtime", **ANSWERS}, headers=as_user(it)).json()["id"]
    client.post(f"/api/tickets/{tid}/comments", json={"body": "Internal HR view", "is_internal": True}, headers=as_user(hr["hr_owner"]))
    # Their own ticket in the portal: yes. Internal notes, history or the agent view: no.
    assert client.get(f"/api/portal/tickets/{tid}", headers=as_user(it)).status_code == 200
    assert client.get(f"/api/tickets/{tid}/comments", headers=as_user(it)).json() == []
    assert client.get(f"/api/tickets/{tid}", headers=as_user(it)).status_code == 404
    assert client.get(f"/api/tickets/{tid}/events", headers=as_user(it)).status_code == 404
    # They reply as a requester: no internal notes, and it doesn't count as staff responding.
    assert client.post(f"/api/tickets/{tid}/comments", json={"body": "x", "is_internal": True}, headers=as_user(it)).status_code == 409
    r = client.post(f"/api/tickets/{tid}/comments", json={"body": "Any update?"}, headers=as_user(it)).json()
    assert r["source"] == "portal"
    db.expire_all()
    assert db.get(Ticket, tid).first_responded_at is None


def test_visibility_settings_permissions(client, hr):
    q = hr["queue"].id
    # Managers can change viewers; others can't; only admins can restrict or unrestrict.
    assert client.put(f"/api/queues/{q}/viewers", json={"user_ids": [hr["it"].id]}, headers=as_user(hr["hr_lead"])).status_code == 200
    assert hr["ticket"] in ids(client, hr["it"])
    assert client.put(f"/api/queues/{q}/viewers", json={"user_ids": []}, headers=as_user(hr["it"])).status_code == 403
    assert client.patch(f"/api/queues/{q}", json={"restricted": False}, headers=as_user(hr["hr_lead"])).status_code == 403
    r = client.patch(f"/api/queues/{q}", json={"restricted": False}, headers=as_user(hr["admin"]))
    assert r.status_code == 200 and r.json()["restricted"] is False


def test_inactive_viewer_group_stops_access(client, db, hr):
    group = db.scalar(select(Group).where(Group.name == "HR auditors"))
    client.patch(f"/api/groups/{group.id}", json={"is_active": False}, headers=as_user(hr["admin"]))
    assert hr["ticket"] not in ids(client, hr["auditor"])


def test_email_reply_to_own_hr_ticket_is_not_staff(db, session_factory, hr):
    from app.mail.intake import IntakeConfig, run_intake
    from tests.test_mail_intake import FakeMailbox

    it = hr["it"]
    t = Ticket(subject="x", source="email", requester_id=it.id, queue_id=hr["queue"].id, status="open", priority="medium")
    db.add(t)
    db.commit()
    mailbox = FakeMailbox()
    mailbox.deliver(from_address=it.email, subject=f"Re: [#{t.id}] x", body_text="from my phone")
    run_intake(session_factory, mailbox, IntakeConfig.create("helpdesk@corp.local", ["corp.local"]))
    db.expire_all()
    [c] = db.scalars(select(Comment).where(Comment.ticket_id == t.id)).all()
    assert not c.is_internal
    assert db.get(Ticket, t.id).first_responded_at is None
