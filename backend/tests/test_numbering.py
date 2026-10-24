# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket numbers and labels: where numbering starts, the label format, and email threading."""

from datetime import datetime

import pytest

from app import numbering
from app.config import settings
from app.mail.text import clean_subject, find_ticket_id, ticket_tag
from app.models import Queue
from tests.conftest import as_user


def test_labels_and_their_patterns(monkeypatch):
    when = datetime(2026, 3, 1).astimezone()
    assert numbering.label(1042, when, "#{n}") == "#1042"
    assert numbering.label(1042, when, "HD-{n}") == "HD-1042"
    assert numbering.label(42, when, "INC{n:06}") == "INC000042"
    assert numbering.label(42, when, "{yyyy}-{n}") == "2026-42"
    assert numbering.pattern("INC{n:06}").fullmatch("INC000042").group(1) == "000042"
    for bad in ["HD", "{n}-{n}", "<script>{n}", "{x}{n}", "x" * 31 + "{n}"]:
        with pytest.raises(numbering.NumberingError):
            numbering.check_format(bad)

    monkeypatch.setattr(settings, "ticket_label_format", "INC{n:06}")
    assert ticket_tag(42) == "[INC000042]"
    assert find_ticket_id("RE: [INC000042] Printer") == 42
    # Old email with the plain tag still threads.
    assert find_ticket_id("RE: [#42] Printer") == 42
    assert clean_subject("Fw: [INC000042] Printer") == "Printer"


def test_settings_set_the_format_and_where_numbers_start(client, db, admin, agent, requester):
    def save(**values):
        return client.put("/api/settings/general", json={"values": values}, headers=as_user(admin))

    assert save(ticket_label_format="HD").status_code == 422
    assert save(ticket_label_format="HD-{n}", ticket_first_number=50000).status_code == 200
    db.add(Queue(name="Desk", is_default=True))
    db.commit()
    t = client.post("/api/tickets", json={"subject": "First", "requester_id": requester.id}, headers=as_user(agent)).json()
    assert (t["id"], t["label"]) == (50000, "HD-50000")
    # With tickets, it can only move forward.
    assert save(ticket_first_number=10).status_code == 200
    assert client.post("/api/tickets", json={"subject": "Second", "requester_id": requester.id}, headers=as_user(agent)).json()["id"] == 50001
    assert save(ticket_first_number=60000).status_code == 200
    assert client.post("/api/tickets", json={"subject": "Third", "requester_id": requester.id}, headers=as_user(agent)).json()["id"] == 60000
