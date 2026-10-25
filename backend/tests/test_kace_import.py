# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic KACE bundles only; no appliance credentials or customer data."""
import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import func, select

from app import kace, storage
from app.models import Attachment, Comment, ImportRef, OutboundEmail, Queue, Ticket, TicketEvent, TicketStatus, TimeEntry, User, UserRole
from tests.conftest import as_user

PAYLOAD=b"Synthetic attachment"


def bundle(*,org="Default",body="A public reply",internal=False,extra=None,checksum=None):
    row={"id":"42","title":"Example printer issue","description":"<p>Paper jam</p>","status":"closed","priority":"high","category":"Printer","source_queue":"Old IT","requester":{"id":"1","name":"Example requester","email":"requester@example.com"},"owner":{"id":"2","name":"Former technician","email":"former@example.com"},"created_at":"2024-03-04T10:00:00-08:00","closed_at":"2024-03-05T10:00:00-08:00","custom_fields":{"custom_1":"Building A"},"notes":[{"id":"10","body":body,"internal":internal,"author":{"id":"2","name":"Former technician","email":"former@example.com"},"created_at":"2024-03-04T11:00:00-08:00","files":[{"id":"5","name":"../../example.txt","path":"files/42/10/5","sha256":checksum or hashlib.sha256(PAYLOAD).hexdigest()}]}],"work":[{"id":"20","minutes":15,"note":"Fixed the jam","author":{"id":"2","name":"Former technician","email":"former@example.com"},"created_at":"2024-03-04T12:00:00-08:00"}]}
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w") as archive:
        archive.writestr("manifest.json",json.dumps({"format":kace.FORMAT,"appliance":"https://kace.corp.example","organization":org,"tickets":1}))
        archive.writestr("tickets.jsonl",json.dumps(row))
        archive.writestr("files/42/10/5",PAYLOAD)
        if extra: archive.writestr(extra,b"unwanted")
    return output.getvalue()


@pytest.fixture
def queue(db):
    q=Queue(name="Imported IT",restricted=True)
    db.add(q);db.commit()
    return q


def count(db,model):
    return db.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("extra",["../outside","/absolute","manifest.json"])
def test_rejects_unsafe_or_duplicate_archive_paths(extra):
    with pytest.raises(kace.KaceError): kace.Bundle(bundle(extra=extra))


def test_checksum_and_queue_fingerprint_are_checked():
    with pytest.raises(kace.KaceError,match="checksum"):
        kace.Bundle(bundle(checksum="0"*64))
    assert kace.fingerprint(bundle(),1)!=kace.fingerprint(bundle(),2)


def test_preview_rolls_back_people_tickets_and_files(db,queue):
    before=count(db,User)
    report=kace.run(db,kace.Bundle(bundle()),queue.id)
    assert report["counts"]["tickets_created"]==1
    assert not report["applied"]
    assert count(db,Ticket)==count(db,Attachment)==count(db,ImportRef)==0
    assert count(db,User)==before
    assert not any(path.is_file() for path in storage.settings.storage_dir.rglob("*"))


def test_import_preserves_privacy_dates_provenance_and_is_repeatable(db,queue,service_desk):
    report=kace.run(db,kace.Bundle(bundle(internal=True)),queue.id,apply=True)
    ticket=db.scalar(select(Ticket))
    assert ticket.status==TicketStatus.closed and ticket.queue_id==queue.id
    assert ticket.created_at.isoformat().startswith("2024-03-04T18:00")
    assert ticket.closed_at.isoformat().startswith("2024-03-05T18:00")
    assert "KACE ticket 42" in ticket.description and ticket.resolution_due_at is None
    assert ticket.imported_fields["KACE custom_1"]=="Building A"
    assert all(not user.is_active and user.password_hash is None for user in db.scalars(select(User)))
    comments=db.scalars(select(Comment)).all()
    assert len(comments)==2 and all(comment.is_internal for comment in comments)
    attachment=db.scalar(select(Attachment))
    assert attachment.filename=="example.txt" and storage.read(attachment.storage_key)==PAYLOAD
    assert db.get(Comment,attachment.comment_id).is_internal
    assert count(db,TimeEntry)==1 and count(db,OutboundEmail)==0
    # Private source notes can never be declassified by a later export.
    again=kace.run(db,kace.Bundle(bundle(body="Updated source note",internal=False)),queue.id,apply=True)
    assert again["counts"]["tickets_updated"]==1
    assert count(db,Ticket)==1 and count(db,Attachment)==1 and count(db,TimeEntry)==1
    assert db.scalar(select(Comment).where(Comment.body=="Updated source note")).is_internal
    assert count(db,TicketEvent)==1
    assert report["applied"]


def test_same_numbers_in_different_organizations_are_distinct(db,queue):
    kace.run(db,kace.Bundle(bundle(org="IT")),queue.id,apply=True)
    kace.run(db,kace.Bundle(bundle(org="Facilities")),queue.id,apply=True)
    assert count(db,Ticket)==2


def test_failed_commit_cleans_saved_files(db,queue,monkeypatch):
    saved=[]
    original=storage.save
    def record(data):
        result=original(data);saved.append(result.key);return result
    monkeypatch.setattr(storage,"save",record)
    monkeypatch.setattr(db,"commit",lambda:(_ for _ in ()).throw(RuntimeError("synthetic commit failure")))
    with pytest.raises(RuntimeError): kace.run(db,kace.Bundle(bundle()),queue.id,apply=True)
    assert saved and all(not storage.path(key).exists() for key in saved)
    assert count(db,Ticket)==0


def test_admin_preview_and_explicit_apply_api(client,db,queue,admin,agent,requester):
    data=bundle()
    def post(path,user,**values):
        return client.post("/api/imports/kace/"+path,headers=as_user(user),data={"queue_id":str(queue.id),**values},files={"file":("trial.zip",data,"application/zip")})
    for user in (agent,requester): assert post("preview",user).status_code==403
    preview=post("preview",admin)
    assert preview.status_code==200,preview.text
    assert count(db,Ticket)==0
    assert post("apply",admin,fingerprint="changed",confirm="true").status_code==409
    assert post("apply",admin,fingerprint=preview.json()["fingerprint"],confirm="false").status_code==409
    result=post("apply",admin,fingerprint=preview.json()["fingerprint"],confirm="true")
    assert result.status_code==200,result.text
    assert result.json()["applied"] and count(db,Ticket)==1
