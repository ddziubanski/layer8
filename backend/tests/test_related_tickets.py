# SPDX-License-Identifier: AGPL-3.0-or-later
from app.models import Device, DeviceSource, Queue, System, Ticket, TicketDevice, TicketSource, TicketStatus, TicketSystem
from app import related
from tests.conftest import as_user


def setup(db, requester):
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    ws, crm = Device(name="WS-0142", source=DeviceSource.manual), System(name="CRM")
    db.add_all([desk, hr, ws, crm])
    db.flush()

    def t(subject, queue=desk, status=TicketStatus.open, parent=None):
        ticket = Ticket(subject=subject, source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id, status=status, parent_id=parent)
        db.add(ticket)
        db.flush()
        return ticket

    me = t("Switch port flapping on 2nd floor")
    same_device = t("Laptop very slow")
    words = t("2nd floor switch keeps dropping")
    one_word = t("Switch for the boardroom")
    closed = t("Port flapping 2nd floor switch", status=TicketStatus.closed)
    restricted = t("Switch port flapping in HR", queue=hr)
    child = t("Switch port flapping again", parent=me.id)
    same_system = t("Login fails")
    db.add_all([TicketDevice(ticket_id=me.id, device_id=ws.id), TicketDevice(ticket_id=same_device.id, device_id=ws.id),
                TicketDevice(ticket_id=restricted.id, device_id=ws.id),
                TicketSystem(ticket_id=me.id, system_id=crm.id), TicketSystem(ticket_id=same_system.id, system_id=crm.id)])
    db.commit()
    return locals()


def test_suggestions_explain_themselves_and_respect_access(client, db, agent, admin, requester):
    s = setup(db, requester)
    got = client.get(f"/api/tickets/{s['me'].id}/related", headers=as_user(agent)).json()
    by_subject = {r["ticket"]["subject"]: r["reasons"] for r in got}
    assert by_subject == {
        "Laptop very slow": ["Same device: WS-0142"],
        "Login fails": ["Same system: CRM"],
        "2nd floor switch keeps dropping": ["Subject shares: 2nd, floor, switch"],
    }
    # Closed tickets, one shared word, the ticket itself and its own child never show; nor, for
    # someone outside HR, a restricted ticket (not even as a count).
    admin_sees = {r["ticket"]["subject"] for r in client.get(f"/api/tickets/{s['me'].id}/related", headers=as_user(admin)).json()}
    assert "Switch port flapping in HR" in admin_sees and "Switch port flapping in HR" not in by_subject


def test_linking_uses_parent_child_and_drops_the_suggestion(client, db, agent, requester):
    s = setup(db, requester)
    h = as_user(agent)
    r = client.patch(f"/api/tickets/{s['words'].id}", json={"parent_id": s["me"].id}, headers=h)
    assert r.status_code == 200, r.text
    assert "2nd floor switch keeps dropping" not in {x["ticket"]["subject"] for x in client.get(f"/api/tickets/{s['me'].id}/related", headers=h).json()}


def test_requesters_cant_ask(client, db, requester):
    s = setup(db, requester)
    assert client.get(f"/api/tickets/{s['me'].id}/related", headers=as_user(requester)).status_code == 403


def test_words():
    assert related.words("RE: Printer on 3rd floor not working!!") == {"printer", "3rd", "floor"}
