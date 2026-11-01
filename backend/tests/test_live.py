# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live updates: a commit that touches a ticket sends one NOTIFY with its id; rollbacks send nothing;
the stream is staff only."""
import asyncio
import json
import os

import psycopg

from app import live
from app.models import Comment, Priority, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user

URL = os.environ.get("HELPDESK_TEST_DATABASE_URL", "postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk_test").replace("+psycopg", "")


def heard(conn) -> list[dict]:
    return [json.loads(n.payload) for n in conn.notifies(timeout=0.5)]


def test_commits_notify_ticket_ids_and_rollbacks_do_not(db, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.commit()
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(f"LISTEN {live.CHANNEL}")
        t = Ticket(subject="Printer offline", source=TicketSource.portal, requester_id=requester.id, queue_id=q.id, status=TicketStatus.new, priority=Priority.medium)
        db.add(t)
        db.flush()
        db.add(Comment(ticket_id=t.id, author_id=requester.id, body="Still offline", source=TicketSource.portal))
        db.commit()
        assert heard(conn) == [{"t": "ticket", "id": t.id}]  # one per ticket per transaction
        t.subject = "Printer offline (2nd floor)"
        db.flush()
        db.rollback()
        assert heard(conn) == []


def test_stream_is_staff_only(client, requester):
    assert client.get("/api/live").status_code == 401
    assert client.get("/api/live", headers=as_user(requester)).status_code == 403


def test_hub_fans_out_to_every_stream():
    async def run():
        a, b = live.hub.subscribe(), live.hub.subscribe()
        try:
            live.hub.publish('{"t": "ticket", "id": 1}')
            return await asyncio.wait_for(a.get(), 1), await asyncio.wait_for(b.get(), 1)
        finally:
            live.hub.unsubscribe(a)
            live.hub.unsubscribe(b)

    assert asyncio.run(run()) == ('{"t": "ticket", "id": 1}', '{"t": "ticket", "id": 1}')
