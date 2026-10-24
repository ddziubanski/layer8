# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multiple queues with owners and managers, as users or groups."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.mail.intake import IntakeConfig, run_intake
from app.models import Comment, OutboundEmail, Queue, UserRole
from tests.conftest import ANSWERS, as_user, make_user
from tests.test_mail_intake import FakeMailbox


@pytest.fixture
def people(db, admin, agent):
    return {
        "admin": admin,
        "park": agent,
        "cole": make_user(db, "dcole@corp.local", UserRole.agent, "D. Cole"),
        "osei": make_user(db, "mosei@corp.local", UserRole.agent, "M. Osei"),
        "user": make_user(db, "ralvarez@corp.local", name="R. Alvarez"),
    }


@pytest.fixture
def network(db, service_desk):
    queue = Queue(name="Network")
    db.add(queue)
    db.commit()
    return queue


def put(client, user, path, **body):
    return client.put(path, json=body, headers=as_user(user))


def new_ticket(client, user):
    return client.post("/api/portal/tickets", json={"subject": "Printer jam", **ANSWERS}, headers=as_user(user)).json()["id"]


def assign(client, user, tid, **body):
    return client.post(f"/api/tickets/{tid}/assign", json=body, headers=as_user(user))


def test_queue_without_owners_accepts_any_agent(client, people, service_desk):
    tid = new_ticket(client, people["user"])
    assert assign(client, people["park"], tid, assignee_id=people["cole"].id).status_code == 200
    names = [u["display_name"] for u in client.get(f"/api/queues/{service_desk.id}/assignable", headers=as_user(people["park"])).json()]
    assert names == ["admin", "D. Cole", "J. Park", "M. Osei"]


def test_only_owners_can_be_assigned(client, people, service_desk):
    r = put(client, people["admin"], f"/api/queues/{service_desk.id}/owners", user_ids=[people["park"].id])
    assert r.status_code == 200
    assert [u["id"] for u in r.json()["owner_users"]] == [people["park"].id]

    tid = new_ticket(client, people["user"])
    refused = assign(client, people["park"], tid, assignee_id=people["cole"].id)
    assert refused.status_code == 409
    assert "not an owner of the Service desk queue" in refused.json()["detail"]
    assert assign(client, people["park"], tid, assignee_id=people["park"].id).status_code == 200


def test_owners_must_be_agents(client, people, service_desk):
    r = put(client, people["admin"], f"/api/queues/{service_desk.id}/owners", user_ids=[people["user"].id])
    assert r.status_code == 422


def test_group_owners_and_my_queues_view(client, db, people, service_desk, network):
    hdr = as_user(people["admin"])
    group = client.post("/api/groups", json={"name": "Network team"}, headers=hdr).json()
    put(client, people["admin"], f"/api/groups/{group['id']}/members", user_ids=[people["cole"].id, people["osei"].id])
    put(client, people["admin"], f"/api/queues/{network.id}/owners", group_ids=[group["id"]])

    names = [u["display_name"] for u in client.get(f"/api/queues/{network.id}/assignable", headers=hdr).json()]
    assert names == ["D. Cole", "M. Osei"]

    tid = new_ticket(client, people["user"])
    # Into Network: Cole can be assigned via the group, Park can't.
    assert assign(client, people["park"], tid, queue_id=network.id, assignee_id=people["park"].id).status_code == 409
    assert assign(client, people["park"], tid, queue_id=network.id, assignee_id=people["cole"].id).status_code == 200

    other = new_ticket(client, people["user"])  # stays in Service desk
    ids = lambda u: [t["id"] for t in client.get("/api/tickets", params={"view": "my_queues"}, headers=as_user(u)).json()["items"]]
    assert ids(people["osei"]) == [tid]
    assert ids(people["park"]) == []
    assert other not in ids(people["cole"])

    # Deactivating the group removes its members' ownership.
    client.patch(f"/api/groups/{group['id']}", json={"is_active": False}, headers=hdr)
    assert ids(people["osei"]) == []
    # And with no active owners left, the queue still counts as having owners configured.
    assert client.get(f"/api/queues/{network.id}/assignable", headers=hdr).json() == []


def test_moving_to_a_queue_the_assignee_doesnt_own_unassigns(client, people, service_desk, network):
    put(client, people["admin"], f"/api/queues/{network.id}/owners", user_ids=[people["cole"].id])
    tid = new_ticket(client, people["user"])
    assign(client, people["park"], tid, assignee_id=people["park"].id)
    moved = assign(client, people["park"], tid, queue_id=network.id).json()
    assert moved["queue"]["name"] == "Network" and moved["assignee"] is None
    events = client.get(f"/api/tickets/{tid}/events", headers=as_user(people["park"])).json()
    event = [e for e in events if e["type"] == "assigned"][-1]
    assert event["data"]["after"] == {"queue_id": network.id, "assignee_id": None}


def test_directory_groups_need_an_id_and_sync_owns_their_members(client, people):
    hdr = as_user(people["admin"])
    assert client.post("/api/groups", json={"name": "SG-Network", "source": "directory"}, headers=hdr).status_code == 422
    g = client.post(
        "/api/groups",
        json={"name": "SG-Network", "source": "directory", "directory_id": "3f2a9c1e-0000-4000-8000-000000000001"},
        headers=hdr,
    ).json()
    assert put(client, people["admin"], f"/api/groups/{g['id']}/members", user_ids=[people["cole"].id]).status_code == 409


def test_managers_edit_their_queue_and_owners_but_not_managers(client, people, service_desk, network):
    put(client, people["admin"], f"/api/queues/{network.id}/managers", user_ids=[people["osei"].id])
    osei, park = people["osei"], people["park"]

    assert client.patch(f"/api/queues/{network.id}", json={"description": "Switches, Wi-Fi"}, headers=as_user(osei)).status_code == 200
    assert put(client, osei, f"/api/queues/{network.id}/owners", user_ids=[people["cole"].id]).status_code == 200
    assert client.patch(f"/api/queues/{network.id}", json={"is_default": True}, headers=as_user(osei)).status_code == 403
    assert put(client, osei, f"/api/queues/{network.id}/managers", user_ids=[osei.id]).status_code == 403
    # Not a manager of this queue.
    assert client.patch(f"/api/queues/{network.id}", json={"description": "x"}, headers=as_user(park)).status_code == 403
    assert client.patch(f"/api/queues/{service_desk.id}", json={"description": "x"}, headers=as_user(osei)).status_code == 403


def test_escalation_notifies_managers_and_their_reply_is_internal(client, db, people, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    desk = Queue(name="Service desk", is_default=True)
    tier2 = Queue(name="Tier 2")
    db.add_all([desk, tier2])
    db.commit()
    desk.escalation_queue_id = tier2.id
    db.commit()
    hdr = as_user(people["admin"])
    group = client.post("/api/groups", json={"name": "Tier 2 leads"}, headers=hdr).json()
    put(client, people["admin"], f"/api/groups/{group['id']}/members", user_ids=[people["osei"].id])
    put(client, people["admin"], f"/api/queues/{tier2.id}/managers", user_ids=[people["cole"].id], group_ids=[group["id"]])

    tid = new_ticket(client, people["user"])
    client.post(f"/api/tickets/{tid}/escalate", json={"reason": "VIP printer"}, headers=as_user(people["cole"]))

    notices = db.scalars(select(OutboundEmail).where(OutboundEmail.is_staff_notice)).all()
    # Cole escalated it himself, so only Osei (via the group) is told.
    assert [n.to_address for n in notices] == ["mosei@corp.local"]
    assert f"[#{tid}]" not in notices[0].subject
    assert "Reason: VIP printer" in notices[0].body

    mailbox = FakeMailbox()
    from app.mail import outbox
    from app.sla import utcnow

    with session_factory() as s:
        outbox.send_due(s, mailbox, utcnow())
    notice_id = db.scalar(select(OutboundEmail.internet_message_id).where(OutboundEmail.is_staff_notice))
    mailbox.deliver(
        from_address="mosei@corp.local",
        subject="RE: Escalated to Tier 2",
        headers={"in-reply-to": notice_id},
        body_text="Who's on site today?",
    )
    run_intake(session_factory, mailbox, IntakeConfig.create("helpdesk@corp.local", ["corp.local"]))
    db.expire_all()
    move_note, reply = db.scalars(select(Comment).order_by(Comment.id)).all()
    assert move_note.body.startswith("Moved from Service desk to Tier 2 by D. Cole.")
    assert reply.body == "Who's on site today?"
    assert reply.ticket_id == tid and reply.is_internal


def test_queue_move_leaves_internal_note_with_reason(client, db, people, service_desk, network):
    tid = new_ticket(client, people["user"])
    r = assign(client, people["park"], tid, queue_id=network.id, note="Looks like a switch problem")
    assert r.status_code == 200
    comments = client.get(f"/api/tickets/{tid}/comments", headers=as_user(people["park"])).json()
    assert [(c["body"], c["is_internal"], c["author"]["id"]) for c in comments] == [
        ("Moved from Service desk to Network by J. Park.\n\nLooks like a switch problem", True, people["park"].id)
    ]
    # Requesters never see it.
    assert client.get(f"/api/tickets/{tid}/comments", headers=as_user(people["user"])).json() == []
    # Reassigning within the same queue adds no note.
    assign(client, people["park"], tid, assignee_id=people["cole"].id)
    assert len(client.get(f"/api/tickets/{tid}/comments", headers=as_user(people["park"])).json()) == 1


def test_managers_can_move_tickets_out_of_their_queue(client, db, people, service_desk, network):
    put(client, people["admin"], f"/api/queues/{network.id}/managers", user_ids=[people["osei"].id])
    put(client, people["admin"], f"/api/queues/{service_desk.id}/owners", user_ids=[people["park"].id])
    tid = new_ticket(client, people["user"])
    assign(client, people["park"], tid, queue_id=network.id)
    # Osei manages Network but owns neither queue, and can still move it on.
    r = assign(client, people["osei"], tid, queue_id=service_desk.id, note="Not a network issue")
    assert r.status_code == 200 and r.json()["queue"]["name"] == "Service desk"
