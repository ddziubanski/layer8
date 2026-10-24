# SPDX-License-Identifier: AGPL-3.0-or-later
"""API keys and the integration API (for apps such as a server-room sign-in log)."""

from sqlalchemy import select

from app.config import settings
from app.models import Comment, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user


def ticket(db, q, requester, subject, **over):
    t = Ticket(subject=subject, source=TicketSource.agent, queue_id=q.id, requester_id=requester.id, **{"status": TicketStatus.open, **over})
    db.add(t)
    db.commit()
    return t


def test_keys_lookup_search_and_notes(client, db, admin, agent, requester, monkeypatch):
    monkeypatch.setattr(settings, "ticket_label_format", "HD-{n}")
    desk, hr = Queue(name="Desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.commit()
    entry = ticket(db, desk, requester, "MER ENTRY: server room, Oct")
    ticket(db, desk, requester, "MER ENTRY old", status=TicketStatus.closed)
    secret = ticket(db, hr, requester, "MER ENTRY payroll")

    made = client.post("/api/api-keys", json={"name": "MER server room"}, headers=as_user(admin)).json()
    token = made["token"]
    assert token.startswith("l8k_") and made["prefix"] == token[:8]
    assert client.post("/api/api-keys", json={"name": "x"}, headers=as_user(agent)).status_code == 403
    key = {"Authorization": f"Bearer {token}"}

    # Look up by number, #number or label; restricted queues stay hidden.
    for ref in (str(entry.id), f"%23{entry.id}", f"HD-{entry.id}"):  # "#" sent encoded
        r = client.get(f"/api/integrations/tickets/{ref}", headers=key)
        assert r.status_code == 200 and r.json()["label"] == f"HD-{entry.id}", ref
    assert client.get(f"/api/integrations/tickets/{secret.id}", headers=key).status_code == 404
    found = client.get("/api/integrations/tickets", params={"q": "MER ENTRY"}, headers=key).json()
    assert [t["id"] for t in found] == [entry.id]

    # Notes are internal and signed by the key's account.
    r = client.post(f"/api/integrations/tickets/HD-{entry.id}/notes", json={"text": "Signed in: J. Park, 09:14"}, headers=key)
    assert r.status_code == 201, r.text
    note = db.scalar(select(Comment).where(Comment.ticket_id == entry.id))
    assert note.is_internal and note.author.display_name == "MER server room (API key)" and note.source.value == "api"

    # The key works only on the integration API, and its account can't sign in or be picked.
    assert client.get("/api/tickets", headers=key).status_code == 401
    assert note.author.is_active is False
    assert "MER server room" not in str(client.get(f"/api/queues/{desk.id}/assignable", headers=as_user(agent)).json())

    # Revoked: stops at once; listed with when it was last used.
    kid = made["id"]
    keys = client.get("/api/api-keys", headers=as_user(admin)).json()
    assert keys[0]["last_used_at"] is not None and "token" not in keys[0]
    client.delete(f"/api/api-keys/{kid}", headers=as_user(admin))
    assert client.get(f"/api/integrations/tickets/{entry.id}", headers=key).status_code == 401
    assert client.get(f"/api/integrations/tickets/{entry.id}", headers={"Authorization": "Bearer l8k_nope"}).status_code == 401


def test_technicians_can_use_it_too(client, db, agent, requester):
    desk = Queue(name="Desk", is_default=True)
    db.add(desk)
    db.commit()
    t = ticket(db, desk, requester, "Printer")
    assert client.get(f"/api/integrations/tickets/{t.id}", headers=as_user(agent)).json()["subject"] == "Printer"
    assert client.get(f"/api/integrations/tickets/{t.id}", headers=as_user(requester)).status_code == 403
