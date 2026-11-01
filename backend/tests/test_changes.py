# SPDX-License-Identifier: AGPL-3.0-or-later
"""Changes: risk, required plans, approval by someone else when the risk calls for it, standard and emergency changes, ticket notes."""

from app.config import settings
from app.models import Comment, Group, Queue, Ticket, TicketSource, UserRole
from tests.conftest import as_user, make_user

PLANS = {"implementation_plan": "Upgrade the stack one member at a time", "rollback_plan": "Boot the previous image", "planned_start": "2026-11-01T22:00:00Z", "planned_end": "2026-11-01T23:30:00Z"}


def act(client, user, cid, action, note=""):
    return client.post(f"/api/changes/{cid}/actions", json={"action": action, "note": note}, headers=as_user(user))


def test_normal_change_needs_approval_by_someone_else(client, db, agent, admin, requester):
    q = Queue(name="Desk", is_default=True)
    db.add(q)
    db.flush()
    t = Ticket(subject="Core switch firmware", source=TicketSource.agent, queue_id=q.id, requester_id=requester.id)
    db.add(t)
    db.commit()
    body = {"title": "Core switch firmware 17.1", "impact": 3, "likelihood": 2, "ticket_ids": [t.id]}
    r = client.post("/api/changes", json=body, headers=as_user(agent))
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["label"], c["risk"], c["risk_level"], c["needs_approval"], c["status"], c["owner"]["id"]) == (f"CHG-{c['id']:05d}", 6, "high", True, "draft", agent.id)
    # Not without plans and a start.
    assert "rollback plan" in act(client, agent, c["id"], "submit").json()["detail"]
    client.put(f"/api/changes/{c['id']}", json={**body, **PLANS}, headers=as_user(agent))
    assert act(client, agent, c["id"], "submit").json()["status"] == "awaiting_approval"
    assert act(client, agent, c["id"], "start").status_code == 409
    # Agents aren't approvers by default; admins are, but not for their own change.
    assert act(client, agent, c["id"], "approve").status_code == 409
    assert act(client, admin, c["id"], "reject").json()["detail"] == "Say why it's rejected"
    got = act(client, admin, c["id"], "approve", "Go ahead in the window")
    assert got.json()["status"] == "approved" and got.json()["approved_by"]["id"] == admin.id
    assert act(client, agent, c["id"], "start").json()["status"] == "in_progress"
    assert act(client, agent, c["id"], "roll_back").json()["detail"] == "Say what happened"
    done = act(client, agent, c["id"], "roll_back", "Stack member 2 didn't rejoin; previous image booted").json()
    assert done["status"] == "rolled_back" and done["outcome"].startswith("Stack member 2")
    assert [e["type"] for e in done["events"]] == ["created", "edited", "submitted", "approved", "started", "rolled_back"]
    notes = [x.body for x in db.query(Comment).filter_by(ticket_id=t.id)]
    assert any("has started" in n for n in notes) and any("was rolled back: Stack member 2" in n for n in notes)
    assert [x["id"] for x in client.get(f"/api/tickets/{t.id}/changes", headers=as_user(agent)).json()] == [c["id"]]
    # Ended: no edits.
    assert client.put(f"/api/changes/{c['id']}", json=body, headers=as_user(agent)).status_code == 409


def test_low_risk_needs_no_approval_and_editing_resets(client, db, agent, admin, monkeypatch):
    c = client.post("/api/changes", json={"title": "New printer queue", **PLANS}, headers=as_user(agent)).json()
    assert c["risk_level"] == "low" and not c["needs_approval"]
    assert act(client, agent, c["id"], "submit").json()["status"] == "approved"
    # At "every change" it would have needed approval; editing one awaiting approval sends it back to draft.
    monkeypatch.setattr(settings, "change_approval_level", "low")
    c2 = client.post("/api/changes", json={"title": "Firewall rule", **PLANS}, headers=as_user(agent)).json()
    assert act(client, agent, c2["id"], "submit").json()["status"] == "awaiting_approval"
    edited = client.put(f"/api/changes/{c2['id']}", json={"title": "Firewall rule (port 443 only)", **PLANS}, headers=as_user(agent)).json()
    assert edited["status"] == "draft" and "Back to draft" in edited["events"][-1]["note"]


def test_approvers_group(client, db, agent, admin, monkeypatch):
    board = make_user(db, "board@corp.local", UserRole.agent, "Board Member")
    db.add(Group(name="Change Board", members=[db.merge(board)]))
    db.commit()
    monkeypatch.setattr(settings, "change_approvers_group", "change board")
    c = client.post("/api/changes", json={"title": "Risky", "impact": 3, "likelihood": 3, **PLANS}, headers=as_user(agent)).json()
    act(client, agent, c["id"], "submit")
    assert act(client, admin, c["id"], "approve").status_code == 409  # not on the board
    assert client.get(f"/api/changes/{c['id']}", headers=as_user(board)).json()["can_approve"]
    assert act(client, board, c["id"], "approve").json()["status"] == "approved"


def test_standard_and_emergency(client, db, agent, admin):
    assert client.post("/api/change-templates", json={"name": "Patch ring 1", "pre_approved": True}, headers=as_user(admin)).status_code == 422
    tpl = client.post("/api/change-templates", json={"name": "Patch ring 1", "pre_approved": True, "rollback_plan": "Uninstall the update", "impact": 2, "likelihood": 2}, headers=as_user(admin)).json()
    assert client.post("/api/change-templates", json={"name": "x"}, headers=as_user(agent)).status_code == 403
    # Standard: from a pre-approved template only, and approved on submit whatever the risk.
    assert client.post("/api/changes", json={"title": "Patching", "kind": "standard"}, headers=as_user(agent)).status_code == 422
    std = client.post("/api/changes", json={"title": "October patches, ring 1", "kind": "standard", "template_id": tpl["id"], "impact": 3, "likelihood": 3, **PLANS}, headers=as_user(agent)).json()
    assert act(client, agent, std["id"], "submit").json()["status"] == "approved"
    # Emergency: starts at once (it still needs a rollback plan), approved afterwards.
    em = client.post("/api/changes", json={"title": "Block attacker IP", "kind": "emergency", "impact": 2, "likelihood": 3}, headers=as_user(agent)).json()
    assert act(client, agent, em["id"], "start").json()["detail"] == "Even an emergency change needs a rollback plan"
    client.put(f"/api/changes/{em['id']}", json={"title": "Block attacker IP", "kind": "emergency", "impact": 2, "likelihood": 3, "rollback_plan": "Remove the rule"}, headers=as_user(agent))
    started = act(client, agent, em["id"], "start").json()
    assert started["status"] == "in_progress" and started["events"][-1]["note"] == "Emergency: started before approval"
    act(client, agent, em["id"], "complete")
    late = act(client, admin, em["id"], "approve").json()
    assert late["status"] == "completed" and late["approved_at"]
    assert [x["id"] for x in client.get("/api/changes", params={"status": "completed"}, headers=as_user(agent)).json()] == [em["id"]]
    assert {x["id"] for x in client.get("/api/changes", params={"open_only": True}, headers=as_user(agent)).json()} == {std["id"]}


def test_change_templates_from_the_answers_file(db):
    from app import cli
    from app.models import ChangeTemplate

    cli.import_settings(db, {"change_templates": [{"name": "Patch ring 1", "pre_approved": True, "rollback_plan": "Uninstall", "impact": 2}]})
    db.commit()
    t = db.query(ChangeTemplate).one()
    assert t.pre_approved and t.impact == 2 and t.rollback_plan == "Uninstall"


def test_overlapping_windows_and_calendar_range(client, db, agent):
    from app.models import Device

    sw = Device(name="SW-CORE-1")
    db.add(sw)
    db.commit()
    a = client.post("/api/changes", json={"title": "Firmware", "planned_start": "2026-11-01T22:00:00Z", "planned_end": "2026-11-01T23:30:00Z", "device_ids": [sw.id]}, headers=as_user(agent)).json()
    b = client.post("/api/changes", json={"title": "Cabling", "planned_start": "2026-11-01T23:00:00Z", "device_ids": [sw.id]}, headers=as_user(agent)).json()
    client.post("/api/changes", json={"title": "Later", "planned_start": "2026-11-02T01:00:00Z"}, headers=as_user(agent))
    got = client.get(f"/api/changes/{a['id']}", headers=as_user(agent)).json()["conflicts"]
    assert [(x["change"]["id"], x["shared"]) for x in got] == [(b["id"], ["SW-CORE-1"])]
    # A cancelled one doesn't count.
    act(client, agent, b["id"], "cancel")
    assert client.get(f"/api/changes/{a['id']}", headers=as_user(agent)).json()["conflicts"] == []
    month = client.get("/api/changes", params={"starts_from": "2026-11-01T00:00:00Z", "starts_before": "2026-11-02T00:00:00Z"}, headers=as_user(agent)).json()
    assert [x["title"] for x in month] == ["Firmware", "Cabling"]


def test_post_change_incident_correlation_and_success_rate(client, db, agent, admin, requester, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import Device, IpAddress, NetworkObservation, Subnet, System, TicketDevice, TicketSystem

    t0 = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
    desk = Queue(name="Desk", is_default=True)
    hr = Queue(name="HR", restricted=True)
    sw = Device(name="SW-CORE-1")
    pc = Device(name="PC-JONES", assigned_user_id=requester.id)
    branch_fw = Device(name="FW-BRANCH-1")
    branch_pc = Device(name="PC-BRANCH-9")
    phones = System(name="Phone System")
    sub_2f = Subnet(network="10.0.20.0/24", name="2nd Floor", site="HQ")
    sub_br_a = Subnet(network="10.2.0.0/24", name="Branch Core", site="Branch")
    sub_br_b = Subnet(network="10.2.10.0/24", name="Branch Users", site="Branch")
    db.add_all([desk, hr, sw, pc, branch_fw, branch_pc, phones, sub_2f, sub_br_a, sub_br_b])
    db.flush()

    db.add_all([
        IpAddress(address="10.0.20.2", device_id=sw.id, mac="00:11:22:33:44:01"),
        IpAddress(address="10.0.20.55", device_id=pc.id, mac="00:11:22:33:44:55"),
        IpAddress(address="10.2.0.1", device_id=branch_fw.id, mac="00:11:22:33:55:01"),
        IpAddress(address="10.2.10.99", device_id=branch_pc.id, mac="00:11:22:33:55:99"),
        NetworkObservation(
            provider="unifi",
            source_id="site-hq",
            source_name="HQ",
            mac="00:11:22:33:44:55",
            address="10.0.20.55",
            uplink_name="SW-CORE-1",
            port="ge-0/0/12",
            observed_at=t0,
        ),
    ])
    # Originating request ticket opened BEFORE the change window.
    req_ticket = Ticket(
        subject="Schedule switch & phone maintenance",
        source=TicketSource.agent,
        queue_id=desk.id,
        requester_id=requester.id,
        created_at=t0 - timedelta(days=2),
    )
    db.add(req_ticket)
    db.commit()

    # Change 1: completed on SW-CORE-1 and Phone System, linked to the pre-existing request ticket.
    c1 = client.post(
        "/api/changes",
        json={
            "title": "Core switch and phone upgrade",
            "implementation_plan": "Upgrade firmware",
            "rollback_plan": "Boot backup image",
            "planned_start": t0.isoformat(),
            "planned_end": (t0 + timedelta(hours=1)).isoformat(),
            "ticket_ids": [req_ticket.id],
            "device_ids": [sw.id],
            "system_ids": [phones.id],
        },
        headers=as_user(agent),
    ).json()
    act(client, agent, c1["id"], "submit")
    act(client, agent, c1["id"], "start")
    act(client, agent, c1["id"], "complete")

    # Change 2: completed on FW-BRANCH-1 at Site Branch.
    c2 = client.post(
        "/api/changes",
        json={
            "title": "Branch firewall rule cleanup",
            "implementation_plan": "Tighten rules",
            "rollback_plan": "Restore previous ruleset",
            "planned_start": t0.isoformat(),
            "planned_end": (t0 + timedelta(hours=1)).isoformat(),
            "device_ids": [branch_fw.id],
        },
        headers=as_user(agent),
    ).json()
    act(client, agent, c2["id"], "submit")
    act(client, agent, c2["id"], "start")
    act(client, agent, c2["id"], "complete")

    # Both completed without post-change incidents so far -> 100% success rate.
    st = client.get("/api/changes/stats", headers=as_user(agent)).json()
    assert st["ended_count"] == 2 and st["successful_count"] == 2 and st["success_rate_pct"] == 100.0

    # Tickets arriving 2 hours after the change window:
    # 1) On requester's PC-JONES (connected to switch SW-CORE-1 and in subnet 10.0.20.0/24).
    t_sw = Ticket(
        subject="No network on 2nd floor",
        source=TicketSource.portal,
        queue_id=desk.id,
        requester_id=requester.id,
        computer_id=pc.id,
        created_at=t0 + timedelta(hours=2),
    )
    # 2) On System Phone System.
    t_sys = Ticket(
        subject="Desk phones not registering",
        source=TicketSource.agent,
        queue_id=desk.id,
        requester_id=requester.id,
        created_at=t0 + timedelta(hours=3),
    )
    # 3) On PC-BRANCH-9 (different subnet 10.2.10.0/24, same Site Branch as FW-BRANCH-1).
    t_site = Ticket(
        subject="Branch printer unreachable",
        source=TicketSource.agent,
        queue_id=desk.id,
        requester_id=requester.id,
        created_at=t0 + timedelta(hours=2),
    )
    # 4) In restricted HR queue (hidden from agent).
    t_hr = Ticket(
        subject="HR phone down",
        source=TicketSource.agent,
        queue_id=hr.id,
        requester_id=requester.id,
        computer_id=sw.id,
        created_at=t0 + timedelta(hours=2),
    )
    # 5) Outside the 72h correlation window.
    t_late = Ticket(
        subject="Next week glitch",
        source=TicketSource.agent,
        queue_id=desk.id,
        requester_id=requester.id,
        computer_id=sw.id,
        created_at=t0 + timedelta(hours=100),
    )
    db.add_all([t_sw, t_sys, t_site, t_hr, t_late])
    db.flush()
    db.add(TicketSystem(ticket_id=t_sys.id, system_id=phones.id))
    db.add(TicketDevice(ticket_id=t_site.id, device_id=branch_pc.id))
    db.commit()

    # Ticket -> change suggestions ("possibly related to CHG-...").
    sug_sw = client.get(f"/api/tickets/{t_sw.id}/change-suggestions", headers=as_user(agent)).json()
    assert [(s["change"]["id"], s["scope"]) for s in sug_sw] == [(c1["id"], "Switch SW-CORE-1")]
    sug_sys = client.get(f"/api/tickets/{t_sys.id}/change-suggestions", headers=as_user(agent)).json()
    assert [(s["change"]["id"], s["scope"]) for s in sug_sys] == [(c1["id"], "System Phone System")]
    sug_site = client.get(f"/api/tickets/{t_site.id}/change-suggestions", headers=as_user(agent)).json()
    assert [(s["change"]["id"], s["scope"]) for s in sug_site] == [(c2["id"], "Site Branch")]
    assert client.get(f"/api/tickets/{t_late.id}/change-suggestions", headers=as_user(agent)).json() == []

    # Change -> ticket review shows suggested post-change tickets (and hides HR from agent).
    got_c1 = client.get(f"/api/changes/{c1['id']}", headers=as_user(agent)).json()
    assert got_c1["follow_up_tickets"] == []
    assert {(s["ticket"]["id"], s["scope"]) for s in got_c1["suggested_tickets"]} == {
        (t_sw.id, "Switch SW-CORE-1"),
        (t_sys.id, "System Phone System"),
    }
    # Admin also sees the HR ticket suggestion.
    got_c1_admin = client.get(f"/api/changes/{c1['id']}", headers=as_user(admin)).json()
    assert any(s["ticket"]["id"] == t_hr.id and s["scope"] == "Device SW-CORE-1" for s in got_c1_admin["suggested_tickets"])

    # Turning the window down to 0 disables suggestions.
    monkeypatch.setattr(settings, "change_correlation_hours", 0)
    assert client.get(f"/api/tickets/{t_sw.id}/change-suggestions", headers=as_user(agent)).json() == []
    monkeypatch.setattr(settings, "change_correlation_hours", 72)

    # Confirming & linking a suggested ticket works on an already-completed change and feeds the success rate.
    linked = client.post(f"/api/changes/{c1['id']}/tickets", json={"ticket_id": t_sw.id}, headers=as_user(agent))
    assert linked.status_code == 200, linked.text
    after = linked.json()
    assert [t["id"] for t in after["follow_up_tickets"]] == [t_sw.id]
    assert [s["ticket"]["id"] for s in after["suggested_tickets"]] == [t_sys.id]
    notes = [x.body for x in db.query(Comment).filter_by(ticket_id=t_sw.id)]
    assert any(c1["label"] in n for n in notes)

    # Change 1 now counts as completed with follow-up incidents -> success rate is 1 of 2 (50%).
    st2 = client.get("/api/changes/stats", headers=as_user(agent)).json()
    assert (
        st2["ended_count"] == 2
        and st2["successful_count"] == 1
        and st2["completed_with_incidents_count"] == 1
        and st2["follow_up_tickets_count"] == 1
        and st2["success_rate_pct"] == 50.0
    )

    # Reports dataset "changes" reflects the same change success rate.
    rep = client.post(
        "/api/reports/run",
        json={"dataset": "changes", "measures": ["ended", "successful", "with_incidents", "follow_up_tickets", "success_pct"]},
        headers=as_user(agent),
    ).json()
    assert rep["rows"] == [[2, 1, 1, 1, 50.0]]

    # Unlinking restores it.
    unlinked = client.delete(f"/api/changes/{c1['id']}/tickets/{t_sw.id}", headers=as_user(agent)).json()
    assert unlinked["follow_up_tickets"] == []
    assert client.get("/api/changes/stats", headers=as_user(agent)).json()["success_rate_pct"] == 100.0
