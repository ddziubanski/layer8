# SPDX-License-Identifier: AGPL-3.0-or-later
"""Track-It! import from an export bundle. Everything here is made up: no real Track-It! data."""

import json
import zipfile

import pytest
from sqlalchemy import select

from app import storage, trackit
from app.models import Attachment, Comment, Device, FieldList, FieldOption, ImportRef, TicketDevice, KbArticle, KbAudience, Priority, Queue, Ticket, TicketStatus, User, UserRole


def bundle(path, tables: dict, files: dict | None = None, manifest: dict | None = None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest or {"format": trackit.FORMAT, "version": 1}))
        for name, rows in tables.items():
            z.writestr(f"tables/{name}.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
        for seq, data in (files or {}).items():
            z.writestr(f"files/{seq}", data)
    return str(path)


TABLES = {
    "_CUSTOMER_": [
        {"SEQUENCE": 1, "FNAME": "Ana", "NAME": "Lopez", "_EMAILID_": "alopez@corp.local", "WINUSERID": "CORP\\alopez", "SSPWD": "hash!", "SALT": "salt!", "_INACTIVE_:": 0, "DEPT": 7},
        {"SEQUENCE": 2, "FNAME": "Old", "NAME": "Timer", "_EMAILID_": "", "WINUSERID": "", "_INACTIVE_:": 1, "LOCATION": 4},
    ],
    "_PERSONNEL_": [
        {"SEQUENCE": 10, "FNAME": "Dee", "NAME": "Cole", "_EMAILID_": "", "WINUSERID": "dcole", "PWD": "x", "SID": "S-1-5"},
        {"SEQUENCE": 11, "FNAME": "Gone", "NAME": "Tech", "_EMAILID_": "gone@corp.local", "WINUSERID": ""},
    ],
    "_DEPART_": [{"SEQUENCE": 7, "NAME": "Finance"}],
    "_LOCATION_": [{"SEQUENCE": 4, "NAME": "HQ"}],
    "_CATEGORIES_": [{"SEQUENCE": 3, "FULLPATH": "Hardware\\Printer", "DESCRIPTION": "Printer"}],
    "_PRIORITY_": [{"SEQUENCE": 1, "PRIORITY_ID": "1 - Critical"}, {"SEQUENCE": 4, "PRIORITY_ID": "4 - Low (next week)"}],
    "_UDSTATUS_": [{"SEQUENCE": 5, "ID": "Waiting on customer", "DESCRIPTION": "Waiting on customer"}],
    "_TELMASTE_": [
        {"SEQUENCE": 1001, "DESCRIPTION": "Printer jammed", "NOTE": "", "STATUS": "C", "CLIENT": 1, "SENT TO": 10, "CATEGORY": 3, "SEQ_PRIORITY": 4,
         "DATE OPEN": "2026-10-12T09:00:00", "CLOSED ON": "2026-10-13T10:00:00", "LASTMODIFIED": "2026-10-13T10:00:00", "_EMAILID_": "abc", "PHONE": "x4321",
         "SEQ_INVITEM": 1, "DEPT": 7, "DUE_DATE:": "2026-10-14T09:00:00"},
        {"SEQUENCE": 1002, "DESCRIPTION": "VPN down", "NOTE": "<p>From the <b>notes</b> field</p>", "STATUS": "O", "SEQ_UDSTATUS": 5, "CLIENT": 2, "SENT TO": 11,
         "SEQ_PRIORITY": 1, "DATE OPEN": "2026-10-14T08:00:00", "LASTMODIFIED": "2026-10-14T08:30:00",
         "OPENED_GROUP:": "NETWORK", "CONTACT_TYPE": "Self Service", "LOCATION": 4, "HD_LOOKUP01": "Building A", "HD_LOOKUP02": "", "HD_CCDT01": "2026-10-01T00:00:00"},
        {"SEQUENCE": 1003, "DESCRIPTION": "Who asked?", "STATUS": "O", "CLIENT": 99, "DATE OPEN": "2026-10-15T08:00:00", "PARENT": 1002, "OPENED_GROUP:": "Desktop"},
    ],
    "_TELDETAI_": [
        {"SEQUENCE": 1, "PROBLEM #": 1001, "NOTETYPE": "Ticket Description", "NOTE": "<div>The printer on 2nd floor<br>jams&nbsp;again</div>", "PDATE": "2026-10-12T09:00:00"},
        {"SEQUENCE": 2, "PROBLEM #": 1001, "NOTETYPE": "Technician Note", "NOTE": "<p>Cleared the tray</p>", "PERSON": 10, "ISPRIVATE": 1, "TIME SPENT": 1800.0, "PDATE": "2026-10-12T11:00:00"},
        {"SEQUENCE": 3, "PROBLEM #": 1001, "NOTETYPE": "Ticket Resolution", "NOTE": "Replaced the roller", "PERSON": 10, "ISPRIVATE": 0, "PDATE": "2026-10-13T10:00:00"},
        {"SEQUENCE": 4, "PROBLEM #": 1001, "NOTETYPE": "System Note", "NOTE": "Status changed to Closed", "PDATE": "2026-10-13T10:00:00"},
        {"SEQUENCE": 5, "PROBLEM #": 1002, "NOTETYPE": "Technician Note", "NOTE": "Asked the user", "PERSON": 11, "REQUESTOR#": 0, "ISPRIVATE": 0, "PDATE": "2026-10-14T08:30:00"},
    ],
    "_WORKORD_": [
        {"SEQUENCE": 501, "HDNUM": 1001, "DESCRIPTION": "Order a roller", "NOTE": "Part 123", "STATUS": "C", "CLIENT": 1, "SENT TO": 10, "DATE OPEN": "2026-10-12T12:00:00", "CLOSED ON": "2026-10-13T09:00:00"},
        {"SEQUENCE": 502, "HDNUM": 9999, "DESCRIPTION": "Orphan", "STATUS": "O"},
    ],
    "_WORKDET_": [{"SEQUENCE": 1, "TTNUM": 501, "NOTETYPE": "Technician Note", "NOTE": "Ordered", "PERSON": 10, "PDATE": "2026-10-12T13:00:00"}],
    "_ATTACH_": [{"SEQUENCE": 77, "SEQ_INCIDENT:": 1001, "FILENAME": "C:\\Users\\x\\jam.png", "FILEDATE": "2026-10-12T09:05:00"}, {"SEQUENCE": 78, "SEQ_INCIDENT:": 1001, "FILENAME": "missing.pdf"}],
    "_TOPIC_": [{"SEQUENCE": 9, "NAME": "Printers"}],
    "_SOLUTION_": [
        {"SEQUENCE": 31, "SUMMARY": "Clear a paper jam", "SOLUTION": "<ol><li>Open tray</li><li>Pull paper</li></ol>", "SEQ_TOPIC": 9, "ISPRIVATE": 0},
        {"SEQUENCE": 32, "SUMMARY": "Printer admin password reset", "SOLUTION": "Staff only", "ISPRIVATE": 1},
    ],
    "_INVENTORY_": [
        {"SEQUENCE": 1, "NAME": "WS-0142", "SERIAL_NUMBER": "ABC1234", "COMPUTER_MANUFACTURER": "Dell Inc.", "PUR_DATE": "2022-01-10T00:00:00"},
        {"SEQUENCE": 2, "NAME": "PRN-2F", "SERIAL_NUMBER": "", "PUR_DATE": "2020-05-01T00:00:00", "_INACTIVE_:": 1},
    ],
}


@pytest.fixture
def desk(db, agent):
    q = Queue(name="Desk", is_default=True)
    db.add_all([q, Queue(name="Network"), Queue(name="Desktop support")])
    # An existing AD user and technician to match.
    db.add(User(email="alopez@corp.local", display_name="Ana Lopez", role=UserRole.requester))
    db.add(User(email="dcole@corp.local", display_name="D. Cole", role=UserRole.agent, sam_account_name="dcole"))
    db.add(Device(name="WS-0142", serial="", manufacturer=""))
    db.commit()
    return q


def test_dry_run_saves_nothing(db, desk, tmp_path):
    path = bundle(tmp_path / "b.zip", TABLES, {77: b"\x89PNG fake"})
    before = db.query(Ticket).count()
    r = trackit.run(db, path)
    assert not r.apply and r.counts["ticket_new"] == 3 and r.counts["attachment_new"] == 1
    assert db.query(Ticket).count() == before and db.query(ImportRef).count() == 0 and not list(storage.path("").rglob("*"))
    assert "Dry run (nothing saved):" in r.lines()[0]


def test_import(db, desk, tmp_path):
    path = bundle(tmp_path / "b.zip", TABLES, {77: b"\x89PNG fake"})
    r = trackit.run(db, path, apply=True, queue_map={"Desktop": "Desktop support"}, labels={"HD_LOOKUP01": "Building"})
    assert (r.counts["requester_matched"], r.counts["requester_new"], r.counts["tech_matched"], r.counts["tech_new"]) == (1, 1, 1, 1)

    t = db.scalar(select(Ticket).where(Ticket.subject == "Printer jammed"))
    ana = db.scalar(select(User).where(User.email == "alopez@corp.local"))
    dee = db.scalar(select(User).where(User.email == "dcole@corp.local"))
    assert (t.requester_id, t.assignee_id, t.status, t.priority, t.category, t.queue_id) == (ana.id, dee.id, TicketStatus.closed, Priority.low, "Hardware\\Printer", desk.id)
    assert t.description == "The printer on 2nd floor\njams again\n\n(Track-It! ticket 1001)" and t.source.value == "email" and t.contact_phone == "x4321"
    # No department choice called Finance: kept on the ticket, not added to the setup.
    assert t.department_id is None and t.imported_fields == {"Department": "Finance"} and db.query(FieldOption).count() == 0
    assert t.resolution_due_at.isoformat().startswith("2026-10-14T09:00")
    assert [d.name for d in db.scalars(select(Device).join(TicketDevice, TicketDevice.device_id == Device.id).where(TicketDevice.ticket_id == t.id))] == ["WS-0142"]
    assert t.created_at.isoformat().startswith("2026-10-12T09:00") and t.closed_at.isoformat().startswith("2026-10-13T10:00")
    notes = db.scalars(select(Comment).where(Comment.ticket_id == t.id).order_by(Comment.created_at, Comment.id)).all()
    assert [(c.body.splitlines()[0], c.is_internal) for c in notes] == [
        ("Track-It! history (system notes):", True), ("Cleared the tray", True), ("Resolution:", False),
    ]
    assert notes[1].body == "Cleared the tray\n\n(Time spent: 30 min)"
    # People Track-It! adds get its location as their office; existing (AD) people keep theirs.
    assert db.scalar(select(User).where(User.display_name == "Old Timer")).office == "HQ" and ana.office is None
    # Nobody's Track-It! password came across; unmatched technicians are inactive.
    gone = db.scalar(select(User).where(User.email == "gone@corp.local"))
    assert gone.role == UserRole.agent and not gone.is_active and gone.password_hash is None
    old = db.scalar(select(User).where(User.display_name == "Old Timer"))
    assert old.email == "trackit-requester-2@import.invalid" and not old.is_active and old.password_hash is None
    assert db.scalar(select(User).where(User.email == "alopez@corp.local")).department is None  # an existing user's details aren't changed

    vpn = db.scalar(select(Ticket).where(Ticket.subject == "VPN down"))
    assert (vpn.status, vpn.priority, vpn.description.splitlines()[0]) == (TicketStatus.waiting_on_user, Priority.critical, "From the notes field")
    # Opened group named like a queue; the rest of Track-It!'s fields in the details line.
    assert vpn.queue.name == "Network" and vpn.source.value == "portal"
    assert vpn.description.endswith("\n\n(Track-It! ticket 1002)")
    assert vpn.imported_fields == {"Location": "HQ", "Ticket source": "Self Service", "Building": "Building A", "Custom Date 1": "2026-10-01"}
    orphan = db.scalar(select(Ticket).where(Ticket.subject == "Who asked?"))
    assert orphan.requester.email == "trackit-import@import.invalid" and orphan.parent_id == vpn.id and orphan.queue.name == "Desktop support"
    assert any("requester 99" in p for p in r.problems) and any("work order 502" in p for p in r.problems)

    # The work order became a child ticket with its note.
    wo = db.scalar(select(Ticket).where(Ticket.subject == "Order a roller"))
    assert wo.parent_id == t.id and wo.status == TicketStatus.closed
    assert [c.body for c in db.scalars(select(Comment).where(Comment.ticket_id == wo.id))] == ["Ordered"]

    att = db.scalar(select(Attachment).where(Attachment.ticket_id == t.id))
    assert (att.filename, att.content_type, storage.read(att.storage_key)) == ("jam.png", "image/png", b"\x89PNG fake") and r.counts["attachment_missing"] == 1

    kb = {a.title: a for a in db.scalars(select(KbArticle))}
    assert kb["Clear a paper jam"].body.startswith("- Open tray\n- Pull paper") and kb["Clear a paper jam"].category == "Printers"
    assert kb["Clear a paper jam"].audience == KbAudience.everyone and kb["Printer admin password reset"].audience == KbAudience.staff

    ws = db.scalar(select(Device).where(Device.name == "WS-0142"))
    assert (ws.serial, ws.manufacturer, str(ws.purchased_on)) == ("ABC1234", "Dell Inc.", "2022-01-10") and db.query(Device).count() == 2
    assert not db.scalar(select(Device).where(Device.name == "PRN-2F")).is_active

    # A later, newer bundle (cut-over) updates and adds only what's new.
    tables = {**TABLES, "_TELMASTE_": [{**TABLES["_TELMASTE_"][1], "STATUS": "C", "CLOSED ON": "2026-10-20T09:00:00"}],
              "_TELDETAI_": [*TABLES["_TELDETAI_"], {"SEQUENCE": 6, "PROBLEM #": 1002, "NOTETYPE": "Ticket Resolution", "NOTE": "Fixed", "PERSON": 11, "PDATE": "2026-10-20T09:00:00"}],
              "_WORKORD_": [], "_ATTACH_": []}
    r2 = trackit.run(db, bundle(tmp_path / "b2.zip", tables), apply=True)
    assert (r2.counts["ticket_new"], r2.counts["ticket_updated"], r2.counts["comment_new"], r2.counts["requester_new"]) == (0, 1, 1, 0)
    db.expire_all()
    assert db.get(Ticket, vpn.id).status == TicketStatus.closed and db.query(Ticket).count() == 4


def test_not_a_bundle(db, desk, tmp_path):
    (tmp_path / "x.zip").write_bytes(b"nope")
    with pytest.raises(trackit.ImportError_, match="Not a Track-It! export bundle"):
        trackit.run(db, str(tmp_path / "x.zip"))
    with pytest.raises(trackit.ImportError_, match="format"):
        trackit.run(db, bundle(tmp_path / "y.zip", {}, manifest={"format": "other"}))
    with pytest.raises(trackit.ImportError_, match="No queue called"):
        trackit.run(db, bundle(tmp_path / "z.zip", {}), queue_name="Nope")
    with pytest.raises(trackit.ImportError_, match="Unknown Track-It! field"):
        trackit.run(db, bundle(tmp_path / "z.zip", {}), labels={"HD_NOPE": "x"})


def test_text():
    assert trackit.text("<p>One</p><p>Two &amp; three</p>") == "One\n\nTwo & three"
    assert trackit.text("plain &lt;text&gt;\r\nline") == "plain <text>\nline"
    assert trackit.priority_of("2 - High (same day)") == Priority.high and trackit.priority_of("") == Priority.medium


def test_field_choices(db, desk, tmp_path):
    db.add(FieldOption(list=FieldList.department, label="finance"))
    db.commit()
    rows = [{**TABLES["_TELMASTE_"][1], "HD_LOOKUP03": " N/A ", "HD_LOOKUP04": "  Floor   2 "}]
    path = bundle(tmp_path / "b.zip", {**TABLES, "_TELMASTE_": [{**rows[0], "DEPT": 7}], "_WORKORD_": []})
    trackit.run(db, path, apply=True, skip={"location", "HD_LOOKUP01"}, labels={"HD_LOOKUP04": "Floor"})
    t = db.scalar(select(Ticket).where(Ticket.subject == "VPN down"))
    # Matched to the existing choice (any case); skipped fields and junk values left out; spaces tidied.
    assert t.department.label == "finance" and t.imported_fields == {"Ticket source": "Self Service", "Custom Date 1": "2026-10-01", "Floor": "Floor 2"}
    # Adding departments is a choice.
    path2 = bundle(tmp_path / "c.zip", {**TABLES, "_DEPART_": [{"SEQUENCE": 8, "NAME": "Legal"}], "_TELMASTE_": [{**TABLES["_TELMASTE_"][0], "SEQUENCE": 2001, "DEPT": 8}], "_WORKORD_": []})
    trackit.run(db, path2, apply=True, departments="add")
    assert db.scalar(select(Ticket).where(Ticket.description.contains("ticket 2001"))).department.label == "Legal"


def test_detect(tmp_path):
    found = {f["column"]: f for f in trackit.detect(trackit.Bundle(bundle(tmp_path / "b.zip", TABLES)))}
    assert found["DEPT"]["used"] == 1 and found["DEPT"]["top"] == [("Finance", 1)] and found["DEPT"]["total"] == 3
    assert found["HD_LOOKUP01"]["name"] == "Custom Look Up 1" and "HD_LOOKUP02" not in found and found["OPENED_GROUP"]["distinct"] == 2
