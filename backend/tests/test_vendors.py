# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vendors, their contacts, the systems they support, and linking systems to tickets."""

from app.models import Device, Queue, Ticket, TicketEvent, TicketSource
from tests.conftest import as_user

MERAKI = {
    "name": "Cisco Meraki", "account_number": "EX-00123", "support_phone": "+1 555 0142",
    "support_portal": "https://dashboard.meraki.com", "support_hours": "24x7", "contract_level": "Enterprise license",
    "contract_ends": "2027-06-30",
    "contacts": [
        {"name": "Meraki support", "role": "support", "email": "support@meraki.example", "phone": "+1 555 0142"},
        {"name": "Sam Rivera", "role": "account manager", "email": "srivera@meraki.example", "mobile": "+1 555 0100"},
    ],
}


def test_vendor_with_contacts(client, agent, admin):
    r = client.post("/api/vendors", json=MERAKI, headers=as_user(agent))
    assert r.status_code == 201, r.text
    v = r.json()
    assert [c["name"] for c in v["contacts"]] == ["Meraki support", "Sam Rivera"] and v["contract_ends"] == "2027-06-30"
    assert client.post("/api/vendors", json={**MERAKI, "name": "cisco meraki"}, headers=as_user(agent)).status_code == 409
    # Edit: keep the support contact (by id), drop Sam, add someone.
    support = v["contacts"][0]
    body = {**MERAKI, "contacts": [{**support}, {"name": "Escalations", "role": "escalation", "phone": "+1 555 0199"}]}
    r = client.put(f"/api/vendors/{v['id']}", json=body, headers=as_user(agent))
    assert [(c["name"], c["id"] == support["id"]) for c in r.json()["contacts"]] == [("Meraki support", True), ("Escalations", False)]
    assert client.get("/api/vendors", params={"q": "ex-001"}, headers=as_user(agent)).json()[0]["name"] == "Cisco Meraki"
    # Technicians edit; only admins delete.
    assert client.delete(f"/api/vendors/{v['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/vendors/{v['id']}", headers=as_user(admin)).status_code == 204
    # Unsafe URL schemes in support_portal / website are rejected.
    assert client.post("/api/vendors", json={**MERAKI, "support_portal": "javascript:alert(1)"}, headers=as_user(agent)).status_code == 422
    assert client.post("/api/vendors", json={**MERAKI, "website": "data:text/html,hi"}, headers=as_user(agent)).status_code == 422


def test_system_with_vendor_devices_addresses_and_tickets(client, db, agent, requester):
    v = client.post("/api/vendors", json=MERAKI, headers=as_user(agent)).json()
    sw1, sw2 = Device(name="SW-CORE-1"), Device(name="SW-CORE-2")
    db.add_all([sw1, sw2])
    db.commit()
    body = {
        "name": "Core switching", "description": "The core switch pair", "vendor_id": v["id"], "contact_id": v["contacts"][0]["id"],
        "device_ids": [sw1.id, sw2.id],
        "addresses": [{"address": "10.0.0.1", "kind": "vip", "notes": "Gateway"}, {"address": "10.0.255.11", "kind": "management"}],
    }
    r = client.post("/api/systems", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["vendor"]["support_phone"] == "+1 555 0142" and s["contact"]["name"] == "Meraki support"
    assert [d["name"] for d in s["devices"]] == ["SW-CORE-1", "SW-CORE-2"] and [a["kind"] for a in s["addresses"]] == ["vip", "management"]
    # Found by address, device or vendor.
    for q in ("10.0.255", "core-2", "meraki"):
        assert [x["name"] for x in client.get("/api/systems", params={"q": q}, headers=as_user(agent)).json()] == ["Core switching"], q
    # The contact must be the vendor's.
    other = client.post("/api/vendors", json={"name": "Other", "contacts": [{"name": "Bob"}]}, headers=as_user(agent)).json()
    assert client.put(f"/api/systems/{s['id']}", json={**body, "contact_id": other["contacts"][0]["id"]}, headers=as_user(agent)).status_code == 422

    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="Core uplink down", source=TicketSource.agent, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    r = client.post(f"/api/tickets/{t.id}/systems", json={"system_id": s["id"]}, headers=as_user(agent))
    assert r.status_code == 201 and r.json()[0]["system"]["vendor"]["name"] == "Cisco Meraki"
    assert len(client.post(f"/api/tickets/{t.id}/systems", json={"system_id": s["id"]}, headers=as_user(agent)).json()) == 1
    assert client.delete(f"/api/tickets/{t.id}/systems/{s['id']}", headers=as_user(agent)).json() == []
    assert [e.type for e in db.query(TicketEvent).filter_by(ticket_id=t.id).order_by(TicketEvent.id)] == ["system_linked", "system_unlinked"]
    assert client.get(f"/api/tickets/{t.id}/systems", headers=as_user(requester)).status_code == 403
