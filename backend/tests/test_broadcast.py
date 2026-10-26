# SPDX-License-Identifier: AGPL-3.0-or-later
"""Update everyone affected (a parent ticket and its open children), optionally resolving them."""
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import Comment, FieldList, FieldOption, OutboundEmail, Queue, Ticket, TicketSource, TicketStatus, UserRole
from tests.conftest import as_user, make_user

MESSAGE = "The 2nd-floor switch has been replaced; you should be back online."


@pytest.fixture
def outage(db, requester, monkeypatch):
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    desk, hr = Queue(name="Service desk", is_default=True), Queue(name="HR", restricted=True)
    db.add_all([desk, hr])
    db.flush()
    other = make_user(db, "mosei@corp.local", name="M. Osei")
    imported = make_user(db, "trackit-requester-9@import.invalid", name="Old Import")

    def t(subject, who, queue=desk, parent=None, status=TicketStatus.open):
        ticket = Ticket(subject=subject, source=TicketSource.portal, queue_id=queue.id, requester_id=who.id, status=status, parent_id=parent)
        db.add(ticket)
        db.flush()
        return ticket

    parent = t("Switch down, 2nd floor", requester)
    made = {
        "parent": parent,
        "same_person": t("No network at desk 4", requester, parent=parent.id),
        "other": t("Can't print", other, parent=parent.id),
        "imported": t("Old report", imported, parent=parent.id),
        "closed": t("Already sorted", other, parent=parent.id, status=TicketStatus.closed),
        "hr": t("HR laptop offline", other, queue=hr, parent=parent.id),
    }
    db.commit()
    return made


def preview(client, user, parent, **extra):
    return client.post(f"/api/tickets/{parent.id}/broadcast/preview", json={"body": MESSAGE, **extra}, headers=as_user(user))


def test_preview_sends_nothing_and_says_who_hears(client, db, agent, outage):
    r = preview(client, agent, outage["parent"])
    assert r.status_code == 200, r.text
    got = r.json()
    rows = {x["ticket"]["subject"]: (x["emailed"], x["why_no_email"]) for x in got["tickets"]}
    # Children first, then the parent; one email per person; HR (restricted) isn't there or counted.
    assert list(rows) == ["No network at desk 4", "Can't print", "Old report", "Switch down, 2nd floor"]
    assert rows["No network at desk 4"] == (True, None)
    assert rows["Switch down, 2nd floor"] == (False, "already emailed for another of these tickets")
    assert rows["Old report"][0] is False
    assert (got["emails"], got["skipped_closed"], got["applied"]) == (2, 1, False)
    assert db.scalar(select(func.count()).select_from(Comment)) == 0
    assert db.scalar(select(func.count()).select_from(OutboundEmail)) == 0


def test_apply_replies_on_each_ticket_and_resolves_them_all(client, db, agent, outage, monkeypatch):
    monkeypatch.setattr(settings, "require_resolution_type", True)
    fixed = FieldOption(list=FieldList.resolution_type, label="Hardware replaced")
    db.add(fixed)
    db.commit()
    # Resolving needs a resolution type here: the preview says so instead of failing later.
    blocked = preview(client, agent, outage["parent"], resolve=True)
    assert blocked.status_code == 422 and "resolution type" in blocked.json()["detail"]
    ok = preview(client, agent, outage["parent"], resolve=True, resolution_type_id=fixed.id).json()
    sent = client.post(f"/api/tickets/{outage['parent'].id}/broadcast", headers=as_user(agent),
                       json={"body": MESSAGE, "resolve": True, "resolution_type_id": fixed.id, "fingerprint": ok["fingerprint"], "confirm": True})
    assert sent.status_code == 200, sent.text
    db.expire_all()
    parent_row = next(x for x in ok["tickets"] if x["ticket"]["id"] == outage["parent"].id)
    # HR's open child (not visible here) keeps the parent open; the reason names no ticket.
    assert parent_row["resolved"] is False and "#" not in parent_row["note"]
    for key in ("same_person", "other", "imported"):
        t = db.get(Ticket, outage[key].id)
        assert t.status == TicketStatus.resolved and t.resolution_type_id == fixed.id
    assert db.get(Ticket, outage["parent"].id).status == TicketStatus.open
    for key in ("parent", "same_person", "other", "imported"):
        t = db.get(Ticket, outage[key].id)
        assert [c.body for c in db.scalars(select(Comment).where(Comment.ticket_id == t.id, Comment.is_internal.is_(False)))] == [MESSAGE]
    assert db.get(Ticket, outage["hr"].id).status == TicketStatus.open and db.get(Ticket, outage["closed"].id).status == TicketStatus.closed


def test_resolving_all_resolves_the_parent_when_nothing_else_is_open(client, db, agent, outage, monkeypatch):
    monkeypatch.setattr(settings, "require_resolution_type", False)
    hr = db.get(Ticket, outage["hr"].id)
    hr.status = TicketStatus.closed
    db.commit()
    ok = preview(client, agent, outage["parent"], resolve=True).json()
    assert all(x["resolved"] for x in ok["tickets"])
    client.post(f"/api/tickets/{outage['parent'].id}/broadcast", headers=as_user(agent),
                json={"body": MESSAGE, "resolve": True, "fingerprint": ok["fingerprint"], "confirm": True})
    db.expire_all()
    assert db.get(Ticket, outage["parent"].id).status == TicketStatus.resolved
    replies = db.scalars(select(OutboundEmail).where(OutboundEmail.body.contains("switch has been replaced"))).all()
    assert sorted(e.to_address for e in replies) == ["mosei@corp.local", "ralvarez@corp.local"]


def test_apply_needs_the_reviewed_preview(client, db, agent, outage):
    ok = preview(client, agent, outage["parent"]).json()
    url = f"/api/tickets/{outage['parent'].id}/broadcast"
    assert client.post(url, json={"body": MESSAGE, "fingerprint": ok["fingerprint"]}, headers=as_user(agent)).status_code == 422
    changed = client.post(url, json={"body": MESSAGE + " Edited.", "fingerprint": ok["fingerprint"], "confirm": True}, headers=as_user(agent))
    assert changed.status_code == 409
    assert db.scalar(select(func.count()).select_from(Comment)) == 0


def test_nothing_linked_or_not_staff(client, db, agent, requester, outage):
    lone = Ticket(subject="On its own", source=TicketSource.portal, queue_id=outage["parent"].queue_id, requester_id=requester.id)
    db.add(lone)
    db.commit()
    r = preview(client, agent, lone)
    assert r.status_code == 422 and "child tickets" in r.json()["detail"]
    assert preview(client, requester, outage["parent"]).status_code == 403
    assert UserRole.requester == requester.role
