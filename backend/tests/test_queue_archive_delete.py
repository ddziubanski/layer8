# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retiring a queue: archive (hidden, tickets kept) or delete (inactive first, tickets moved)."""
from sqlalchemy import select

from app.models import Comment, Queue, RoutingRule, Ticket, TicketEvent, TicketSource, TicketStatus, Wallboard
from tests.conftest import as_user


def setup(db, requester, agent):
    desk, old = Queue(name="Service desk", is_default=True), Queue(name="Old projects")
    db.add_all([desk, old])
    db.flush()
    open_ = Ticket(subject="Still going", source=TicketSource.portal, queue_id=old.id, requester_id=requester.id)
    done = Ticket(subject="Long finished", source=TicketSource.portal, queue_id=old.id, requester_id=requester.id,
                  status=TicketStatus.closed, assignee_id=agent.id)
    db.add_all([open_, done])
    db.commit()
    return desk, old, open_, done


def test_archive_hides_and_keeps_tickets(client, db, admin, agent, requester):
    desk, old, open_, _ = setup(db, requester, agent)
    url = f"/api/queues/{old.id}/archive"
    assert client.post(url, json={"archived": True}, headers=as_user(agent)).status_code == 403
    r = client.post(url, json={"archived": True}, headers=as_user(admin))
    assert r.status_code == 200 and r.json()["archived_at"] and r.json()["is_active"] is False
    db.expire_all()
    assert db.get(Ticket, open_.id).queue_id == old.id
    # Archived queues can't be switched back on until unarchived.
    assert client.patch(f"/api/queues/{old.id}", json={"is_active": True}, headers=as_user(admin)).status_code == 409
    back = client.post(url, json={"archived": False}, headers=as_user(admin)).json()
    assert back["archived_at"] is None and back["is_active"] is False
    # The default queue can't be archived.
    assert client.post(f"/api/queues/{desk.id}/archive", json={"archived": True}, headers=as_user(admin)).status_code == 409


def test_delete_needs_inactive_first_then_moves_tickets(client, db, admin, agent, requester):
    desk, old, open_, done = setup(db, requester, agent)
    desk_id, old_id = desk.id, old.id
    board = Wallboard(name="Desk", queue_ids=[desk.id, old.id], token_hash="0" * 64, prefix="wb_test")
    db.add(board)
    db.commit()
    url = f"/api/queues/{old.id}"
    assert client.delete(url, headers=as_user(agent)).status_code == 403
    active = client.delete(url, headers=as_user(admin))
    assert active.status_code == 409 and "inactive" in active.json()["detail"]
    client.patch(url, json={"is_active": False}, headers=as_user(admin))
    check = client.get(f"{url}/delete-check", headers=as_user(admin)).json()
    assert check == {"tickets": 2, "open_tickets": 1, "blockers": []}
    unmoved = client.delete(url, headers=as_user(admin))
    assert unmoved.status_code == 409 and "2 ticket" in unmoved.json()["detail"]
    assert client.delete(f"{url}?move_to={old.id}", headers=as_user(admin)).status_code == 422
    assert client.delete(f"{url}?move_to={desk.id}", headers=as_user(admin)).status_code == 204
    db.expire_all()
    assert db.get(Queue, old_id) is None
    moved, closed = db.get(Ticket, open_.id), db.get(Ticket, done.id)
    assert moved.queue_id == desk_id and closed.queue_id == desk_id
    # Closed history keeps who did the work; both tickets say what happened.
    assert closed.assignee_id == agent.id and closed.status == TicketStatus.closed
    assert any("was deleted" in c.body for c in db.scalars(select(Comment).where(Comment.ticket_id == moved.id)))
    assert db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == closed.id)).all()
    assert db.get(Wallboard, board.id).queue_ids == [desk_id]


def test_delete_refuses_while_still_in_use(client, db, admin, agent, requester):
    desk, old, *_ = setup(db, requester, agent)
    old.is_active = False
    db.add(RoutingRule(name="Projects mail", set_queue_id=old.id))
    db.commit()
    check = client.get(f"/api/queues/{old.id}/delete-check", headers=as_user(admin)).json()
    assert check["blockers"] == ["Routing rule Projects mail uses it"]
    r = client.delete(f"/api/queues/{old.id}?move_to={desk.id}", headers=as_user(admin))
    assert r.status_code == 409 and "Routing rule Projects mail" in r.json()["detail"]
    db.expire_all()
    assert db.get(Queue, old.id) is not None and db.get(Ticket, db.scalar(select(Ticket.id).where(Ticket.subject == "Still going"))).queue_id == old.id
