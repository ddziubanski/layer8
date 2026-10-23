from datetime import timedelta

from app import sla
from app.models import Priority, Queue, ServiceHeartbeat, Ticket, TicketSource, TicketStatus, Wallboard
from tests.conftest import as_user


def make_board(client, admin, **body):
    made = client.post("/api/wallboards", json={"name": "Service desk TV", **body}, headers=as_user(admin))
    assert made.status_code == 201, made.text
    return made.json()


def show(client, token):
    return client.get("/api/public/wallboard", headers={"Authorization": f"Bearer {token}"})


def test_only_admins_manage_boards_and_the_link_is_shown_once(client, db, admin, agent):
    assert client.post("/api/wallboards", json={"name": "TV"}, headers=as_user(agent)).status_code == 403
    assert client.get("/api/wallboards", headers=as_user(agent)).status_code == 403
    made = make_board(client, admin)
    assert made["token"].startswith("l8w_") and made["prefix"] == made["token"][:8]
    stored = db.get(Wallboard, made["id"])
    assert made["token"] not in (stored.token_hash, stored.prefix)
    listed = client.get("/api/wallboards", headers=as_user(admin)).json()
    assert "token" not in listed[0] and listed[0]["name"] == "Service desk TV"


def test_revoked_or_replaced_links_stop_working(client, admin):
    made = make_board(client, admin)
    assert show(client, made["token"]).status_code == 200
    assert show(client, "l8w_wrong").status_code == 401
    assert client.get("/api/public/wallboard").status_code == 401
    renewed = client.post(f"/api/wallboards/{made['id']}/link", headers=as_user(admin)).json()
    assert show(client, made["token"]).status_code == 401
    assert show(client, renewed["token"]).status_code == 200
    assert client.delete(f"/api/wallboards/{made['id']}", headers=as_user(admin)).json()["is_active"] is False
    gone = show(client, renewed["token"])
    assert gone.status_code == 401 and "switched off" in gone.json()["detail"]


def test_board_counts_lists_and_alerts(client, db, admin, agent, requester):
    now = sla.utcnow()
    desk, private = Queue(name="Service desk"), Queue(name="HR cases", restricted=True)
    db.add_all([desk, private])
    db.flush()
    db.add(ServiceHeartbeat(name="worker", last_seen=now, info={}))

    def ticket(queue, **kw):
        t = Ticket(subject=kw.pop("subject", "Printer jam"), requester_id=requester.id, queue_id=queue.id, source=TicketSource.portal, **kw)
        db.add(t)
        return t

    old = ticket(desk, subject="VPN down", status=TicketStatus.new, priority=Priority.critical)
    ticket(desk, status=TicketStatus.open, assignee_id=agent.id, sla_at_risk_at=now - timedelta(minutes=5), resolution_due_at=now + timedelta(hours=1))
    late = ticket(desk, subject="Laptop broken", status=TicketStatus.open, assignee_id=agent.id, sla_at_risk_at=now - timedelta(hours=2), resolution_due_at=now - timedelta(minutes=10))
    ticket(desk, status=TicketStatus.resolved, resolved_at=now)
    ticket(private, subject="Never on the TV", status=TicketStatus.new)
    db.flush()
    old.created_at = now - timedelta(hours=1)
    db.commit()

    made = make_board(client, admin)
    data = show(client, made["token"]).json()
    from datetime import datetime, time
    today = datetime.combine(now.astimezone().date(), time.min).astimezone()
    made_today = 4 if old.created_at >= today else 3  # "VPN down" is an hour old: yesterday just after midnight
    assert data["totals"] == {"open": 3, "unassigned": 1, "sla_at_risk": 1, "sla_breached": 1, "waiting_on_user": 0, "new_today": made_today, "resolved_today": 1}
    assert [t["subject"] for t in data["unassigned"]] == ["VPN down"]
    assert data["unassigned"][0]["requester"] == requester.display_name
    assert [t["subject"] for t in data["sla"]] == ["Laptop broken", "Printer jam"]
    assert data["sla"][0]["breached"] is True and data["sla"][0]["assignee"] == agent.display_name
    assert data["queues"] == [{"name": "Service desk", "open": 3, "unassigned": 1, "breached": 1}]
    assert data["people"] == [{"name": agent.display_name, "open": 2, "needs_attention": 2}]
    texts = [a["text"] for a in data["alerts"]]
    assert texts == [
        "1 ticket past its SLA",
        "1 high or critical priority ticket with nobody assigned",
        "1 ticket unassigned for over 30 min",
        "1 ticket at risk of missing its SLA",
    ]
    assert "Never on the TV" not in str(data) and late.id

    # A restricted queue shows only when chosen.
    chosen = make_board(client, admin, name="HR TV", queue_ids=[private.id])
    hr = show(client, chosen["token"]).json()
    assert [t["subject"] for t in hr["unassigned"]] == ["Never on the TV"]


def test_stopped_worker_raises_an_alert(client, db, admin):
    db.add(ServiceHeartbeat(name="worker", last_seen=sla.utcnow() - timedelta(minutes=30), info={}))
    db.commit()
    data = show(client, make_board(client, admin)["token"]).json()
    assert data["alerts"][0]["level"] == "critical" and "worker isn't running" in data["alerts"][0]["text"]


def test_unknown_queue_is_refused(client, admin):
    r = client.post("/api/wallboards", json={"name": "TV", "queue_ids": [999999]}, headers=as_user(admin))
    assert r.status_code == 422
