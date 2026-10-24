# SPDX-License-Identifier: AGPL-3.0-or-later
"""Purchase orders: numbering, totals, approval, receiving (notes on tickets), files; ticket expenses."""

import pytest

from app.config import settings
from app.models import Comment, Queue, Ticket, TicketSource, Vendor
from tests.conftest import as_user, make_user


@pytest.fixture
def setup(db, agent, admin, requester):
    q = Queue(name="Desk", is_default=True)
    v = Vendor(name="CDW")
    db.add_all([q, v])
    db.flush()
    t = Ticket(subject="New monitors for Finance", source=TicketSource.portal, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    manager = make_user(db, "boss@corp.local", agent.role, "The Boss")
    db.commit()
    return {"vendor": v, "ticket": t, "manager": manager}


def po_body(setup, **over):
    return {
        "title": "Monitors for Finance", "vendor_id": setup["vendor"].id, "vendor_reference": "Quote Q-7781", "shipping": 25,
        "lines": [
            {"item": "Dell P2425 monitor", "part_number": "210-BMHN", "quantity": 4, "unit_price": 199.99, "tax_rate": 8.25},
            {"item": "Monitor arm", "quantity": 4, "unit_price": 45},
        ],
        "ticket_ids": [setup["ticket"].id], **over,
    }


def test_create_number_and_totals(client, agent, setup, monkeypatch):
    r = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent))
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["label"] == "PO-00001" and po["status"] == "draft" and po["requested_by"]["id"] == agent.id
    # 4 x 199.99 = 799.96 + 8.25% = 865.96; 4 x 45 = 180; + 25 shipping
    assert (po["subtotal"], po["tax"], po["total"]) == (979.96, 66.0, 1070.96)
    assert [t["subject"] for t in po["tickets"]] == ["New monitors for Finance"]
    assert [e["type"] for e in po["events"]] == ["created"]
    monkeypatch.setattr(settings, "po_label_format", "{yyyy}-PO{n}")
    assert client.get(f"/api/purchase-orders/{po['id']}", headers=as_user(agent)).json()["label"].endswith("-PO1")
    assert client.get("/api/purchase-orders", params={"q": "210-BMHN"}, headers=as_user(agent)).json()["total"] == 1
    assert client.get("/api/purchase-orders", params={"q": "PO-00001"}, headers=as_user(agent)).json()["total"] == 1


def test_approval_then_ordering(client, agent, admin, setup):
    po = client.post("/api/purchase-orders", json=po_body(setup, approver_id=setup["manager"].id), headers=as_user(agent)).json()
    status = lambda who, s: client.post(f"/api/purchase-orders/{po['id']}/status", json={"status": s}, headers=as_user(who))  # noqa: E731
    assert status(agent, "requested").json()["status"] == "requested"
    assert "approving" in status(agent, "ordered").json()["detail"]
    assert "The Boss" in status(agent, "approved").json()["detail"]
    r = status(setup["manager"], "approved")
    assert r.json()["status"] == "approved" and r.json()["approved_at"]
    r = status(agent, "ordered")
    assert r.json()["status"] == "ordered" and r.json()["order_date"]
    # Received is set by receiving, never by hand.
    assert status(agent, "received").status_code == 422


def test_receiving_notes_tickets_and_protects_lines(client, db, agent, setup):
    po = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent)).json()
    client.post(f"/api/purchase-orders/{po['id']}/status", json={"status": "ordered"}, headers=as_user(agent))
    monitors, arms = (l["id"] for l in po["lines"])
    recv = lambda lines: client.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": lines}, headers=as_user(agent))  # noqa: E731
    r = recv([{"line_id": monitors, "quantity": 2}])
    assert r.json()["status"] == "partially_received"
    assert "can't have arrived" in recv([{"line_id": monitors, "quantity": 3}]).json()["detail"]
    # Received lines can't be removed or cut below what arrived.
    body = po_body(setup)
    body["lines"] = [{**body["lines"][0], "id": monitors, "quantity": 1}, {**body["lines"][1], "id": arms}]
    assert "already received" in client.put(f"/api/purchase-orders/{po['id']}", json=body, headers=as_user(agent)).json()["detail"]
    body["lines"] = [{**po_body(setup)["lines"][1], "id": arms}]
    assert "can't be removed" in client.put(f"/api/purchase-orders/{po['id']}", json=body, headers=as_user(agent)).json()["detail"]
    r = recv([{"line_id": monitors, "quantity": 2}, {"line_id": arms, "quantity": 4}])
    assert r.json()["status"] == "received" and r.json()["lines_received"] == 2
    notes = [c.body for c in db.query(Comment).filter_by(ticket_id=setup["ticket"].id).order_by(Comment.id)]
    assert notes[0].startswith('PO-00001 "Monitors for Finance": Received 2 × Dell P2425 monitor.')
    assert "Everything on it has arrived" in notes[1]


def test_files(client, agent, admin, setup):
    po = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent)).json()
    r = client.post(f"/api/purchase-orders/{po['id']}/attachments", files={"file": ("quote.pdf", b"%PDF-1.4 quote", "application/pdf")}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert client.get(f"/api/po-files/{fid}/view", headers=as_user(agent)).headers["content-type"] == "application/pdf"
    assert client.get(f"/api/po-files/{fid}/download", headers=as_user(agent)).content == b"%PDF-1.4 quote"
    detail = client.get(f"/api/purchase-orders/{po['id']}", headers=as_user(agent)).json()
    assert [a["filename"] for a in detail["attachments"]] == ["quote.pdf"]
    assert client.delete(f"/api/purchase-orders/{po['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/purchase-orders/{po['id']}", headers=as_user(admin)).status_code == 204


def test_ticket_expenses(client, agent, requester, setup):
    t = setup["ticket"]
    po = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent)).json()
    r = client.post(f"/api/tickets/{t.id}/expenses", json={"description": "Courier", "quantity": 1, "unit_cost": 18.5}, headers=as_user(agent))
    assert r.status_code == 201, r.text
    # The linked PO counts towards the ticket until an expense line points at it.
    assert r.json()["total"] == 18.5 + 1070.96
    r = client.post(f"/api/tickets/{t.id}/expenses", json={"description": "Monitors (on the PO)", "quantity": 1, "unit_cost": 1070.96, "po_id": po["id"]}, headers=as_user(agent))
    body = r.json()
    assert body["total"] == 18.5 + 1070.96 and body["expenses"][1]["po_label"] == "PO-00001"
    eid = body["expenses"][0]["id"]
    assert client.delete(f"/api/tickets/{t.id}/expenses/{eid}", headers=as_user(agent)).json()["total"] == 1070.96
    assert client.get(f"/api/tickets/{t.id}/expenses", headers=as_user(requester)).status_code == 403


# The public receiving page and the heads-up to receiving


@pytest.fixture
def ordered(client, db, agent, setup, monkeypatch):
    from app.models import OutboundEmail  # noqa: F401

    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    monkeypatch.setattr(settings, "portal_url", "https://helpdesk.corp.example")
    monkeypatch.setattr(settings, "receiving_email", "dock@corp.local")
    po = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent)).json()
    po = client.post(f"/api/purchase-orders/{po['id']}/status", json={"status": "ordered"}, headers=as_user(agent)).json()
    return po


def test_heads_up_to_receiving_when_ordered(client, db, agent, ordered):
    from app.models import OutboundEmail

    mail = db.query(OutboundEmail).filter_by(to_address="dock@corp.local").one()
    assert mail.subject == "Heads-up: PO-00001 Monitors for Finance is on its way"
    assert "4 × Dell P2425 monitor (210-BMHN)" in mail.body and "199.99" not in mail.body
    assert ordered["receiving_code"] in mail.body and "https://helpdesk.corp.example/receive?po=PO-00001&code=" in mail.body
    assert ordered["receiving_url"].startswith("https://helpdesk.corp.example/receive?po=PO-00001")
    r = client.post(f"/api/purchase-orders/{ordered['id']}/heads-up", json={"to": "warehouse@corp.local", "message": "Big boxes"}, headers=as_user(agent))
    assert r.status_code == 200 and db.query(OutboundEmail).filter_by(to_address="warehouse@corp.local").one().body.startswith("Big boxes")


def test_public_receiving_page(client, db, agent, setup, ordered, monkeypatch):
    from app.api import receiving
    from app.models import OutboundEmail, POAttachment

    receiving._failures.clear()
    code = ordered["receiving_code"]
    look = client.post("/api/public/receiving/lookup", json={"po": "PO-00001", "code": code.lower()})
    assert look.status_code == 200, look.text
    assert [l["item"] for l in look.json()["lines"]] == ["Dell P2425 monitor", "Monitor arm"] and "unit_price" not in look.json()["lines"][0]
    # The plain number works too.
    assert client.post("/api/public/receiving/lookup", json={"po": str(ordered["id"]), "code": code}).status_code == 200

    lines = [{"line_id": l["id"], "quantity": l["quantity"]} for l in look.json()["lines"]]
    r = client.post("/api/public/receiving/receive", json={"po": "PO-00001", "code": code, "name": "Pat at the dock", "lines": lines, "note": "2 boxes"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Received"
    po = client.get(f"/api/purchase-orders/{ordered['id']}", headers=as_user(agent)).json()
    assert po["status"] == "received" and any("Pat at the dock" in (e["data"].get("note") or "") for e in po["events"])
    told = {m.to_address for m in db.query(OutboundEmail).filter(OutboundEmail.subject.like("Delivered:%"))}
    assert agent.email in told
    # Received now: no more receiving.
    assert client.post("/api/public/receiving/lookup", json={"po": "PO-00001", "code": code}).status_code == 200
    r = client.post("/api/public/receiving/photo", data={"po": "PO-00001", "code": code, "name": "Pat"}, files={"file": ("slip.jpg", b"\xff\xd8\xff photo", "image/jpeg")})
    assert r.status_code == 201 and db.query(POAttachment).filter_by(po_id=ordered["id"]).count() == 1


def test_public_receiving_needs_the_code_and_limits_guesses(client, ordered, monkeypatch):
    from app.api import receiving

    receiving._failures.clear()
    for _ in range(receiving.MAX_FAILURES):
        assert client.post("/api/public/receiving/lookup", json={"po": "PO-00001", "code": "WRONG1"}).status_code == 404
    # Locked out now, even with the right code.
    assert client.post("/api/public/receiving/lookup", json={"po": "PO-00001", "code": ordered["receiving_code"]}).status_code == 429
    receiving._failures.clear()
    monkeypatch.setattr(settings, "public_receiving", False)
    assert "turned off" in client.post("/api/public/receiving/lookup", json={"po": "PO-00001", "code": ordered["receiving_code"]}).json()["detail"]


def test_purchase_order_reports(client, admin, agent, setup):
    from app.reports import presets

    po = client.post("/api/purchase-orders", json=po_body(setup), headers=as_user(agent)).json()
    client.post(f"/api/purchase-orders/{po['id']}/status", json={"status": "ordered"}, headers=as_user(agent))
    r = client.post("/api/reports/run", json={"dataset": "purchase_orders", "group_by": ["vendor"], "measures": ["count", "value", "open"]}, headers=as_user(admin))
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [["CDW", 1, 1070.96, 1]]
    for p in presets.PRESETS:
        if p.spec.dataset == "purchase_orders":
            assert client.post("/api/reports/run", json=p.spec.model_dump(mode="json"), headers=as_user(admin)).status_code == 200, p.key
