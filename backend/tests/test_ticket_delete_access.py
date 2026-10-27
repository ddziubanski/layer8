# SPDX-License-Identifier: AGPL-3.0-or-later
"""Queue bulk deletion uses the same per-ticket, group and visibility boundary as single deletion."""
from app import permissions
from app.models import Group, Ticket, TicketSource
from tests.conftest import as_user


def test_group_delete_permission_is_independent_of_bulk_assignment(client, db, agent, requester, service_desk):
    group = Group(name="Example cleanup team", members=[db.merge(agent)])
    ticket = Ticket(subject="Example test ticket", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.agent)
    db.add_all([group, ticket]); db.commit()
    permissions.save(db, {"ticket_delete": {"who": "groups", "group_ids": [group.id]}, "ticket_bulk": {"who": "admins"}}); db.commit()
    me = client.get("/api/auth/me", headers=as_user(agent)).json()
    assert "ticket_delete" in me["permissions"] and "ticket_bulk" not in me["permissions"]
    tid = ticket.id
    assert client.delete(f"/api/tickets/{tid}", headers=as_user(agent)).status_code == 204
    db.expire_all()
    assert db.get(Ticket, tid) is None


def test_delete_grant_does_not_bypass_queue_access_or_revocation(client, db, agent, requester, service_desk):
    group = Group(name="Example cleanup team", members=[db.merge(agent)])
    ticket = Ticket(subject="Example restricted test", requester_id=requester.id, queue_id=service_desk.id, source=TicketSource.agent)
    db.add_all([group, ticket]); db.commit()
    permissions.save(db, {"ticket_delete": {"who": "groups", "group_ids": [group.id]}})
    service_desk.restricted = True; db.commit()
    tid = ticket.id
    assert client.delete(f"/api/tickets/{tid}", headers=as_user(agent)).status_code == 404
    service_desk.restricted = False; group.is_active = False; db.commit()
    assert client.delete(f"/api/tickets/{tid}", headers=as_user(agent)).status_code == 403
    db.expire_all()
    assert db.get(Ticket, tid) is not None
