# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from app.models import Comment, Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user


@pytest.fixture
def tickets(db, requester, agent):
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.flush()

    def t(subject, description="", queue=desk, status=TicketStatus.open):
        ticket = Ticket(subject=subject, description=description, source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id, status=status)
        db.add(ticket)
        db.flush()
        return ticket

    made = {
        "printer": t("Printer jams on 2nd floor", "Paper jam in tray 2"),
        "old": t("VPN down", "Can't connect from home\n\n(Track-It! ticket 17045)", status=TicketStatus.closed),
        "noted": t("Laptop slow"),
        "hr": t("Printer in HR", "Confidential printer", queue=hr),
    }
    db.add(Comment(ticket_id=made["noted"].id, author_id=agent.id, body="Replaced the toner cartridge", is_internal=True, source=TicketSource.agent))
    db.commit()
    return made


def found(client, user, q, **params):
    r = client.get("/api/tickets", params={"q": q, "scope": "all", **params}, headers=as_user(user))
    assert r.status_code == 200, r.text
    return {t["subject"] for t in r.json()["items"]}


def test_search_subject_description_notes_and_numbers(client, agent, tickets):
    assert found(client, agent, "printer") == {"Printer jams on 2nd floor"}  # HR is restricted
    assert found(client, agent, '"paper jam"') == {"Printer jams on 2nd floor"}
    assert found(client, agent, "toner") == {"Laptop slow"}  # an internal note: technicians see it
    assert found(client, agent, "17045") == {"VPN down"}  # the old Track-It! number, closed history included
    assert found(client, agent, f"#{tickets['noted'].id}") == {"Laptop slow"}
    assert found(client, agent, "printer -tray") == set()
    assert found(client, agent, "nothing-like-this") == set()


def test_search_keeps_the_other_filters(client, admin, agent, tickets):
    assert found(client, admin, "printer") == {"Printer jams on 2nd floor", "Printer in HR"}
    assert found(client, admin, "printer", status="closed") == set()
    assert found(client, agent, "vpn", view="open") == set()


def test_blank_or_punctuation_only_search_lists_everything(client, agent, tickets):
    assert len(found(client, agent, "  ")) == len(found(client, agent, "!?")) == 3
