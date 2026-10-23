import json
from datetime import date, datetime, time, timedelta

import httpx
import pytest

from app import configstore, hours, presence, sla
from app.config import settings
from app.models import Queue, User, UserRole
from tests.conftest import as_user

WEEK = "Mon-Fri 08:00-17:00\nSat 09:00-12:00"
# Friday 23 October 2026, local time (tests don't depend on the machine's time zone).
FRI = date(2026, 10, 23)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm)).astimezone()


@pytest.mark.parametrize(
    "when_, open_, edge, said",
    [
        (at(FRI, 10), True, at(FRI, 17), "today at 17:00"),
        (at(FRI, 18), False, at(FRI + timedelta(days=1), 9), "tomorrow at 09:00"),
        (at(FRI, 7, 59), False, at(FRI, 8), "today at 08:00"),
        # Saturday afternoon: Sunday closed, Monday 26th a closed day -> Tuesday.
        (at(FRI + timedelta(days=1), 13), False, at(FRI + timedelta(days=4), 8), "on Tuesday at 08:00"),
    ],
)
def test_open_closed_and_next(when_, open_, edge, said):
    st = hours.status(WEEK, "2026-10-26 Bank holiday", when_)
    assert st.open is open_
    assert (st.closes_at if open_ else st.next_open_at) == edge
    assert hours.when(edge, when_) == said


def test_named_closed_day_and_overnight_stretch():
    st = hours.status(WEEK, "2026-10-23 Staff training", at(FRI, 10))
    assert not st.open and st.closed_for == "Staff training"
    night = hours.status("Fri 20:00-24:00\nSat 00:00-06:00", "", at(FRI, 23))
    assert night.open and night.closes_at == at(FRI + timedelta(days=1), 6)


def test_lines_join_days_and_errors_explain_the_format():
    assert hours.lines("Mon-Fri 08:00-17:00\nTue 08:00-12:00, 13:00-17:00") == ["Mon 08:00-17:00", "Tue 08:00-12:00, 13:00-17:00", "Wed-Fri 08:00-17:00"]
    for bad, says in [("Mon-Fri 9-5", "08:00"), ("Weekdays 08:00-17:00", "Mon-Fri"), ("Mon 17:00-08:00", "isn't after"), ("Mon 08:00", "from-to")]:
        with pytest.raises(hours.HoursError, match=says):
            hours.parse_week(bad)
    with pytest.raises(hours.HoursError, match="2026-12-25"):
        hours.parse_closed("Christmas")


def test_settings_check_hours(db, admin):
    with pytest.raises(configstore.SettingsError, match="08:00"):
        configstore.update(db, "hours", {"desk_hours": "Mon-Fri 9-5"}, admin)
    configstore.update(db, "hours", {"desk_hours": "Mon-Thu 07:30-16:00", "desk_closed_days": "2026-12-25 Christmas Day"}, admin)
    assert settings.desk_hours == "Mon-Thu 07:30-16:00"


def test_queue_hours_are_checked_and_empty_means_desk_hours(client, admin):
    made = client.post("/api/queues", json={"name": "Night shift", "hours": "Mon-Sun 00:00-24:00"}, headers=as_user(admin))
    assert made.status_code == 201, made.text
    assert made.json()["hours"] == "Mon-Sun 00:00-24:00"
    bad = client.patch(f"/api/queues/{made.json()['id']}", json={"hours": "nights"}, headers=as_user(admin))
    assert bad.status_code == 422
    cleared = client.patch(f"/api/queues/{made.json()['id']}", json={"hours": "  "}, headers=as_user(admin))
    assert cleared.json()["hours"] is None


def test_portal_shows_each_queues_hours_message_and_status_only(client, db, requester, agent, monkeypatch):
    monkeypatch.setattr(settings, "desk_hours", "")  # the desk is never open
    monkeypatch.setattr(settings, "desk_closed_days", "")
    monkeypatch.setattr(settings, "after_hours_message", "Nobody in at {queue}. Back {next}.")
    monkeypatch.setattr(settings, "presence_on_portal", True)
    always = Queue(name="Network", show_in_portal=True, hours="Mon-Sun 00:00-24:00", owner_users=[db.merge(agent)])
    closed = Queue(name="Printers", show_in_portal=True)
    db.add_all([always, closed])
    user = db.merge(agent)
    user.presence, user.presence_at = "busy", sla.utcnow()
    db.commit()
    queues = {q["name"]: q for q in client.get("/api/portal/queues", headers=as_user(requester)).json()}
    assert queues["Network"]["hours"]["open_now"] is True and queues["Network"]["hours"]["message"] is None
    assert queues["Network"]["technicians"] == [{"name": agent.display_name, "status": "busy"}]
    assert queues["Printers"]["hours"]["open_now"] is False
    assert queues["Printers"]["hours"]["message"] == "Nobody in at Printers. Back soon."
    assert queues["Printers"]["technicians"] == []
    # Stale status shows as unknown; switched off, no names at all.
    user.presence_at = sla.utcnow() - timedelta(hours=1)
    db.commit()
    assert client.get("/api/portal/queues", headers=as_user(requester)).json()[0]["technicians"][0]["status"] == "unknown"
    monkeypatch.setattr(settings, "presence_on_portal", False)
    assert all(q["technicians"] is None for q in client.get("/api/portal/queues", headers=as_user(requester)).json())
    assert client.get("/api/portal/hours", headers=as_user(requester)).json()["open_now"] is False


def test_presence_looks_up_ids_once_and_reads_status(db, agent, monkeypatch):
    monkeypatch.setattr(settings, "presence_on_portal", True)
    db.add(Queue(name="Desk", show_in_portal=True, owner_users=[db.merge(agent)]))
    db.commit()
    calls = []

    def graph(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.startswith("/v1.0/users/"):
            return httpx.Response(200, json={"id": "11111111-2222-3333-4444-555555555555"})
        assert json.loads(request.content) == {"ids": ["11111111-2222-3333-4444-555555555555"]}
        return httpx.Response(200, json={"value": [{"id": "11111111-2222-3333-4444-555555555555", "availability": "DoNotDisturb", "activity": "Presenting"}]})

    http = httpx.Client(transport=httpx.MockTransport(graph))
    said, did = presence.refresh(db, http=http, token="test-token")
    assert did and "1 of 1" in said
    user = db.get(User, agent.id)
    assert user.entra_id and user.presence == "busy"
    calls.clear()
    presence.refresh(db, http=http, token="test-token")
    assert calls == [("POST", "/v1.0/communications/getPresencesByUserId")]


def test_presence_explains_missing_permission(db, agent, monkeypatch):
    monkeypatch.setattr(settings, "presence_on_portal", True)
    db.add(Queue(name="Desk", show_in_portal=True, owner_users=[db.merge(agent)]))
    db.commit()
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})))
    with pytest.raises(presence.PresenceError, match="User.ReadBasic.All"):
        presence.refresh(db, http=http, token="test-token")


def test_presence_off_does_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "presence_on_portal", False)
    assert presence.refresh(db, http=None, token="unused") == ("Off: Settings > Desk hours", False)
    assert UserRole.agent  # keeps the import used
