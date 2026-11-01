# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem records group visible incidents and keep their investigation notes internal."""

from datetime import timedelta

from app import sla
from app.models import (
    Device,
    IpAddress,
    NetworkInventory,
    NetworkObservation,
    NetworkOutage,
    Priority,
    Queue,
    Ticket,
    TicketStatus,
    TicketSource,
)
from tests.conftest import as_user


def make_ticket(db, requester, queue, subject):
    ticket = Ticket(subject=subject, source=TicketSource.agent, requester_id=requester.id, queue_id=queue.id, status=TicketStatus.open, priority=Priority.medium)
    db.add(ticket)
    db.commit()
    return ticket


def test_create_problem_link_tickets_and_record_updates(client, db, agent, requester):
    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.commit()
    ticket = make_ticket(db, requester, queue, "VPN disconnects after sleep")

    created = client.post("/api/problems", json={
        "title": "VPN clients disconnect after sleep",
        "description": "Several users reported disconnects after waking laptops.",
        "status": "investigating",
        "root_cause": "",
        "workaround": "Reconnect the client.",
        "resolution": "",
        "ticket_ids": [ticket.id],
    }, headers=as_user(agent))
    assert created.status_code == 201, created.text
    problem = created.json()
    assert problem["label"] == f"PRB-{problem['id']:05d}"
    assert problem["status"] == "investigating"
    assert [linked["id"] for linked in problem["tickets"]] == [ticket.id]
    assert [event["type"] for event in problem["updates"]] == ["created", "ticket_linked"]

    note = client.post(f"/api/problems/{problem['id']}/updates", json={"body": "Confirmed with two affected users."}, headers=as_user(agent))
    assert note.status_code == 201, note.text
    assert note.json()["type"] == "note" and note.json()["body"] == "Confirmed with two affected users."

    updated = client.put(f"/api/problems/{problem['id']}", json={
        "title": problem["title"],
        "description": problem["description"],
        "status": "known_error",
        "root_cause": "The VPN adapter driver resets after resume.",
        "workaround": "Reconnect the client.",
        "resolution": "",
        "owner_id": agent.id,
        "ticket_ids": [ticket.id],
    }, headers=as_user(agent))
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "known_error"
    assert updated.json()["root_cause"].startswith("The VPN adapter")
    assert [event["type"] for event in updated.json()["updates"][-2:]] == ["status_changed", "edited"]
    assert client.get("/api/problems", params={"status": "known_error"}, headers=as_user(agent)).json()[0]["id"] == problem["id"]


def test_problem_visibility_requires_access_to_every_linked_ticket(client, db, admin, agent, requester):
    public_queue = Queue(name="Desk", is_default=True)
    restricted_queue = Queue(name="People", restricted=True)
    db.add_all([public_queue, restricted_queue])
    db.commit()
    public_ticket = make_ticket(db, requester, public_queue, "Laptop Wi-Fi drops")
    restricted_ticket = make_ticket(db, requester, restricted_queue, "Confidential account issue")

    hidden_problem = client.post("/api/problems", json={
        "title": "Network driver regression",
        "ticket_ids": [public_ticket.id, restricted_ticket.id],
    }, headers=as_user(admin)).json()
    visible_problem = client.post("/api/problems", json={
        "title": "Wi-Fi driver reports",
        "ticket_ids": [public_ticket.id],
    }, headers=as_user(admin)).json()

    listing = client.get("/api/problems", headers=as_user(agent))
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [visible_problem["id"]]
    assert client.get(f"/api/problems/{hidden_problem['id']}", headers=as_user(agent)).status_code == 404
    assert client.post("/api/problems", json={"title": "Leak check", "ticket_ids": [restricted_ticket.id]}, headers=as_user(agent)).status_code == 404


def test_requesters_cannot_access_problem_management(client, requester):
    assert client.get("/api/problems", headers=as_user(requester)).status_code == 403
    assert client.post("/api/problems", json={"title": "Requester-created"}, headers=as_user(requester)).status_code == 403


def test_timeline_never_reveals_a_ticket_subject(client, db, admin, agent, requester):
    # Unlinking a restricted ticket makes the problem visible to everyone; its history must not
    # carry that ticket's subject along.
    public_queue = Queue(name="Desk", is_default=True)
    restricted_queue = Queue(name="People", restricted=True)
    db.add_all([public_queue, restricted_queue])
    db.commit()
    public_ticket = make_ticket(db, requester, public_queue, "Laptop Wi-Fi drops")
    restricted_ticket = make_ticket(db, requester, restricted_queue, "Confidential account issue")
    problem = client.post("/api/problems", json={"title": "Driver regression", "ticket_ids": [public_ticket.id, restricted_ticket.id]}, headers=as_user(admin)).json()
    r = client.put(f"/api/problems/{problem['id']}", json={"title": "Driver regression", "ticket_ids": [public_ticket.id]}, headers=as_user(admin))
    assert r.status_code == 200, r.text

    seen = client.get(f"/api/problems/{problem['id']}", headers=as_user(agent))
    assert seen.status_code == 200
    assert "Confidential" not in seen.text
    assert [u["body"] for u in seen.json()["updates"] if u["type"].startswith("ticket_")] == [
        f"Linked ticket #{public_ticket.id}", f"Linked ticket #{restricted_ticket.id}", f"Unlinked ticket #{restricted_ticket.id}"]


def test_outage_to_problem_to_everyone_workflow(client, db, admin, agent, requester):
    now = sla.utcnow()
    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.commit()

    t_out1 = make_ticket(db, requester, queue, "Outage: SW-BRANCH-1 offline")
    t_out2 = make_ticket(db, requester, queue, "Outage: AP-BRANCH-1 offline")
    db.add_all([
        NetworkInventory(
            provider="unifi",
            source_id="site-branch",
            device_key="sw-1",
            source_name="Branch",
            name="SW-BRANCH-1",
            kind="switch",
            mac="aa:bb:cc:00:01:01",
            address="10.20.0.2",
            state="offline",
            observed_at=now,
        ),
        NetworkInventory(
            provider="unifi",
            source_id="site-branch",
            device_key="ap-1",
            source_name="Branch",
            name="AP-BRANCH-1",
            kind="ap",
            mac="aa:bb:cc:00:01:02",
            address="10.20.0.3",
            state="offline",
            observed_at=now,
        ),
        NetworkOutage(
            provider="unifi",
            source_id="site-branch",
            device_key="sw-1",
            is_down=True,
            down_since=now - timedelta(minutes=6),
            ticket_id=t_out1.id,
            changed_at=now - timedelta(minutes=6),
        ),
        NetworkOutage(
            provider="unifi",
            source_id="site-branch",
            device_key="ap-1",
            is_down=True,
            down_since=now - timedelta(minutes=4),
            ticket_id=t_out2.id,
            changed_at=now - timedelta(minutes=4),
        ),
    ])
    db.commit()

    # 1. Concurrent drops at the same site offer one outage cluster.
    clusters_res = client.get("/api/problems/outage-clusters", headers=as_user(agent))
    assert clusters_res.status_code == 200, clusters_res.text
    clusters = clusters_res.json()
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["scope_kind"] == "site"
    assert cluster["scope_label"] == "Site Branch"
    assert cluster["devices"] == ["SW-BRANCH-1", "AP-BRANCH-1"]
    assert cluster["ticket_ids"] == sorted([t_out1.id, t_out2.id])

    # 2. Create problem record and draft service notice from the cluster. Drafting a notice
    # needs the notices permission (admins by default); a plain agent is refused.
    draft = {
            "title": cluster["suggested_title"],
            "outage_scope": cluster["scope_label"],
            "create_draft_notice": True,
    }
    assert client.post("/api/problems/from-outage", json=draft, headers=as_user(agent)).status_code == 403
    created_res = client.post(
        "/api/problems/from-outage",
        json={
            "title": cluster["suggested_title"],
            "description": "Power loss in the branch wiring closet.",
            "workaround": "Use cellular hotspot until power returns.",
            "outage_scope": cluster["scope_label"],
            "ticket_ids": cluster["ticket_ids"],
            "create_draft_notice": True,
            "notice_title": cluster["suggested_notice_title"],
            "notice_message": cluster["suggested_notice_message"],
        },
        headers=as_user(admin),
    )
    assert created_res.status_code == 201, created_res.text
    problem = created_res.json()
    assert problem["status"] == "investigating"
    assert problem["outage_scope"] == "Site Branch"
    assert problem["active_outage_devices"] == ["SW-BRANCH-1", "AP-BRANCH-1"]
    assert problem["outage_recovered"] is False
    assert problem["notice"] is not None
    assert problem["notice"]["enabled"] is False
    assert problem["notice"]["state"] == "off"

    # Once covered by an open problem, the cluster is no longer offered again.
    assert client.get("/api/problems/outage-clusters", headers=as_user(agent)).json() == []

    # Publish the draft notice.
    notice_id = problem["notice"]["id"]
    pub_res = client.put(f"/api/notices/{notice_id}/enabled", json={"enabled": True}, headers=as_user(admin))
    assert pub_res.status_code == 200
    assert pub_res.json()["state"] == "current"

    # 3. A new ticket from a person on that switch / at that site is offered "Part of this outage".
    pc = Device(name="PC-BRANCH-5", assigned_user_id=requester.id, is_active=True)
    db.add(pc)
    db.flush()
    db.add_all([
        IpAddress(address="10.20.0.55", hostname="PC-BRANCH-5", mac="aa:bb:cc:00:09:55", device_id=pc.id),
        NetworkObservation(
            provider="unifi",
            source_id="site-branch",
            mac="aa:bb:cc:00:09:55",
            source_name="Branch",
            address="10.20.0.55",
            hostname="PC-BRANCH-5",
            uplink_name="SW-BRANCH-1",
            port="Gi1/0/5",
            observed_at=now,
        ),
    ])
    db.commit()
    t_user = make_ticket(db, requester, queue, "Cannot reach file shares from branch desk")

    tp_res = client.get(f"/api/tickets/{t_user.id}/problems", headers=as_user(agent))
    assert tp_res.status_code == 200, tp_res.text
    suggested = tp_res.json()["suggested"]
    assert len(suggested) == 1
    assert suggested[0]["problem"]["id"] == problem["id"]
    assert "Part of this outage" in suggested[0]["scope"]

    prob_view = client.get(f"/api/problems/{problem['id']}", headers=as_user(agent)).json()
    assert [s["ticket"]["id"] for s in prob_view["suggested_tickets"]] == [t_user.id]

    link_res = client.post(f"/api/tickets/{t_user.id}/problems", json={"problem_id": problem["id"]}, headers=as_user(agent))
    assert link_res.status_code == 200
    assert link_res.json()["suggested"] == []

    # 4. On recovery, the problem flags outage_recovered and offers a broadcast to everyone affected.
    for inv in db.query(NetworkInventory).filter_by(source_id="site-branch").all():
        inv.state = "online"
    for out in db.query(NetworkOutage).filter_by(source_id="site-branch").all():
        out.is_down = False
    db.commit()

    recovered_view = client.get(f"/api/problems/{problem['id']}", headers=as_user(agent)).json()
    assert recovered_view["active_outage_devices"] == []
    assert recovered_view["recovered_outage_devices"] == ["SW-BRANCH-1", "AP-BRANCH-1"]
    assert recovered_view["outage_recovered"] is True

    # Preview broadcast + resolve all.
    preview = client.post(
        f"/api/problems/{problem['id']}/broadcast/preview",
        json={"body": "Branch power and network equipment are back online.", "resolve": True},
        headers=as_user(agent),
    )
    assert preview.status_code == 200, preview.text
    pdata = preview.json()
    assert pdata["applied"] is False
    assert len(pdata["tickets"]) == 3

    # Apply broadcast + resolve all.
    sent = client.post(
        f"/api/problems/{problem['id']}/broadcast",
        json={
            "body": "Branch power and network equipment are back online.",
            "resolve": True,
            "fingerprint": pdata["fingerprint"],
            "confirm": True,
        },
        headers=as_user(agent),
    )
    assert sent.status_code == 200, sent.text
    sdata = sent.json()
    assert sdata["applied"] is True
    assert all(row["resolved"] for row in sdata["tickets"])

    final_problem = client.get(f"/api/problems/{problem['id']}", headers=as_user(agent)).json()
    assert final_problem["status"] == "resolved"
    assert final_problem["notice"]["state"] == "ended"
    assert all(t["status"] == "resolved" for t in final_problem["tickets"])


def test_editing_a_problem_keeps_its_outage_scope_unless_told(client, db, agent):
    created = client.post("/api/problems/from-outage", json={"title": "Branch down", "outage_scope": "Site Branch", "create_draft_notice": False},
                          headers=as_user(agent))
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    # An older client that doesn't know about outage scope leaves it alone.
    kept = client.put(f"/api/problems/{pid}", json={"title": "Branch down", "status": "investigating"}, headers=as_user(agent)).json()
    assert kept["outage_scope"] == "Site Branch"
    cleared = client.put(f"/api/problems/{pid}", json={"title": "Branch down", "status": "investigating", "outage_scope": ""}, headers=as_user(agent)).json()
    assert cleared["outage_scope"] == ""


def test_old_recoveries_at_the_same_site_are_not_part_of_a_new_outage_problem(client, db, agent):
    long_ago = sla.utcnow() - timedelta(days=60)
    db.add_all([
        NetworkInventory(provider="unifi", source_id="site-branch", device_key="sw-9", source_name="Branch", name="SW-OLD", kind="switch",
                         mac="aa:bb:cc:00:09:09", state="online", observed_at=long_ago),
        NetworkOutage(provider="unifi", source_id="site-branch", device_key="sw-9", is_down=False, down_since=None, changed_at=long_ago),
    ])
    db.commit()
    p = client.post("/api/problems/from-outage", json={"title": "Branch down", "outage_scope": "Site Branch", "create_draft_notice": False},
                    headers=as_user(agent)).json()
    assert p["recovered_outage_devices"] == []
    assert p["outage_recovered"] is False
