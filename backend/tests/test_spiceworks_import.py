# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spiceworks Cloud Help Desk CSV import, with made-up rows in the export's column layout."""
import csv

import pytest
from sqlalchemy import func, select

from app import spiceworks
from app.models import FieldOption, Priority, Queue, Ticket, TicketSource, TicketStatus, TimeEntry, User, UserRole
from app.trackit import ImportError_
from tests.conftest import make_user

COLUMNS = ["Ticket Number", "Summary", "Description", "Assigned To", "Category", "Closed On", "Created On", "Created By", "Due On", "Priority",
           "Organization Name", "Status", "Time Spent", "Time To Resolve", "Organization Host", "Link to Ticket", "Department", "Contact Number", "Vendor Ticket"]


def row(number, summary, **over):
    base = {"Ticket Number": str(number), "Summary": summary, "Description": f"{summary}, please", "Assigned To": "", "Category": "", "Closed On": "",
            "Created On": "3/4/2024 2:05 pm UTC", "Created By": "", "Due On": "", "Priority": "medium", "Organization Name": "Example Co", "Status": "Open",
            "Time Spent": "0", "Time To Resolve": "", "Organization Host": "example", "Link to Ticket": f"https://example.on.spiceworks.com/tickets/open/{number}",
            "Department": "", "Contact Number": "", "Vendor Ticket": ""}
    return {**base, **over}


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return str(path)


@pytest.fixture
def maintenance(db):
    q = Queue(name="Maintenance", restricted=True)
    db.add(q)
    db.commit()
    return q


ROWS = [
    row(101, "Leaking sink in break room", **{"Created By": "Ana Lopez", "Assigned To": "Sam Fixit", "Category": "Plumbing", "Status": "Closed",
        "Closed On": "3/5/2024 9:15 am UTC", "Priority": "high", "Department": "Finance", "Time Spent": "45"}),
    row(102, "Light out in hallway", **{"Created By": "visitor@example.com", "Assigned To": "Sam Fixit", "Contact Number": "5550100"}),
    row(103, "Door sticks", **{"Created By": "Two Names", "Description": "x" * 10000}),
]


def test_dry_run_saves_nothing(db, maintenance, tmp_path):
    r = spiceworks.run(db, write(tmp_path / "e.csv", ROWS), queue_name="Maintenance")
    assert r.counts["ticket_new"] == 3 and db.scalar(select(func.count()).select_from(Ticket)) == 0


def test_import_into_the_maintenance_queue(db, maintenance, tmp_path):
    ana = make_user(db, "alopez@corp.local", name="Ana Lopez")
    make_user(db, "two1@corp.local", name="Two Names")
    make_user(db, "two2@corp.local", name="Two Names")  # a name two accounts share isn't guessed
    path = write(tmp_path / "e.csv", ROWS)
    r = spiceworks.run(db, path, queue_name="maintenance", apply=True, departments="add")
    assert (r.counts["ticket_new"], r.counts["requester_matched"], r.counts["requester_new"], r.counts["tech_new"], r.counts["truncated"]) == (3, 1, 2, 1, 1)

    sink = db.scalar(select(Ticket).where(Ticket.subject == "Leaking sink in break room"))
    sam = db.scalar(select(User).where(User.display_name == "Sam Fixit"))
    assert (sink.queue_id, sink.requester_id, sink.assignee_id, sam.role, sam.is_active) == (maintenance.id, ana.id, sam.id, UserRole.agent, False)
    assert (sink.status, sink.priority, sink.category, sink.department.label) == (TicketStatus.closed, Priority.high, "Plumbing", "Finance")
    assert sink.created_at.isoformat().startswith("2024-03-04T14:05") and sink.closed_at.isoformat().startswith("2024-03-05T09:15")
    assert sink.description.endswith("(Spiceworks ticket 101)") and sink.imported_fields == {"Spiceworks link": "https://example.on.spiceworks.com/tickets/open/101"}
    entry = db.scalar(select(TimeEntry).where(TimeEntry.ticket_id == sink.id))
    assert (entry.minutes, entry.user_id) == (45, sam.id)

    light = db.scalar(select(Ticket).where(Ticket.subject == "Light out in hallway"))
    assert (light.status, light.source, light.contact_phone, light.requester.email) == (TicketStatus.open, TicketSource.email, "5550100", "visitor@example.com")
    door = db.scalar(select(Ticket).where(Ticket.subject == "Door sticks"))
    assert door.requester.email.startswith("spiceworks-requester-two.names") and "cut this description off" in door.description
    assert db.scalar(select(FieldOption).where(FieldOption.label == "Finance")) is not None

    # A later export updates the same tickets instead of duplicating them.
    again = spiceworks.run(db, write(tmp_path / "f.csv", [{**ROWS[1], "Status": "Closed", "Closed On": "3/6/2024 8:00 am UTC"}]), queue_name="Maintenance", apply=True)
    assert again.counts["ticket_updated"] == 1 and db.scalar(select(func.count()).select_from(Ticket)) == 3
    db.expire_all()
    assert light.status == TicketStatus.closed


def test_not_an_export_or_no_queue(db, maintenance, tmp_path):
    (tmp_path / "x.csv").write_text("Name,Email\na,b\n")
    with pytest.raises(ImportError_, match="isn't a Spiceworks ticket export"):
        spiceworks.run(db, str(tmp_path / "x.csv"), queue_name="Maintenance")
    with pytest.raises(ImportError_, match="No queue called 'Facilities'"):
        spiceworks.run(db, write(tmp_path / "e.csv", ROWS), queue_name="Facilities")


def test_dates():
    assert spiceworks.when("12/31/2023 11:59 pm UTC").isoformat() == "2023-12-31T23:59:00+00:00"
    assert spiceworks.when("1/2/2019 3:04 am UTC").isoformat() == "2019-01-02T03:04:00+00:00"
    assert spiceworks.when("") is None and spiceworks.when("soon") is None
