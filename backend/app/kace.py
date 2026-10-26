# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded, offline KACE migration. No provider calls or normal ticket side effects."""
import hashlib
import io
import json
import mimetypes
import re
import zipfile
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy import func, select, text

from app import storage
from app.models import Attachment, AuthSource, Comment, EventType, ImportRef, Priority, Queue, ScanStatus, Ticket, TicketEvent, TicketSource, TicketStatus, TimeEntry, User, UserEmail, UserRole
from app.trackit import text as plain_text

FORMAT = "layer8-kace-export-v1"
MAX_UPLOAD = 64 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
MAX_TICKETS = 1000


class KaceError(ValueError):
    pass


class Person(BaseModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)


class File(BaseModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(pattern=r"^files/[0-9]+/[0-9]+/[0-9]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Note(BaseModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    body: str = Field(default="", max_length=1_000_000)
    internal: bool = True  # Missing visibility must never make a private note public.
    author: Person | None = None
    created_at: AwareDatetime | None = None
    files: list[File] = Field(default_factory=list, max_length=100)


class Work(BaseModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    note: str = Field(default="", max_length=10000)
    minutes: int = Field(ge=0, le=5256000)
    author: Person | None = None
    created_at: AwareDatetime | None = None


class Row(BaseModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=1_000_000)
    status: TicketStatus
    priority: Priority
    category: str = Field(default="", max_length=100)
    source_queue: str = Field(default="", max_length=200)
    requester: Person | None = None
    owner: Person | None = None
    created_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    custom_fields: dict[str, str] = Field(default_factory=dict, max_length=50)
    notes: list[Note] = Field(default_factory=list, max_length=5000)
    work: list[Work] = Field(default_factory=list, max_length=5000)


class Manifest(BaseModel):
    format: str
    appliance: str = Field(max_length=500)
    organization: str = Field(min_length=1, max_length=200)
    tickets: int = Field(ge=1, le=MAX_TICKETS)


class Bundle:
    def __init__(self, data: bytes):
        self.unpacked = 0
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            entries = self.zip.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or len(names) > 20000:
                raise KaceError("Duplicate archive entries or too many files")
            if sum(entry.file_size for entry in entries) > MAX_UNPACKED:
                raise KaceError("Bundle expands beyond 256 MiB; export a smaller batch")
            for entry in entries:
                if entry.flag_bits & 1 or entry.file_size > MAX_FILE:
                    raise KaceError("Encrypted archives and entries above 25 MiB are not supported")
                if entry.filename not in ("manifest.json", "tickets.jsonl") and not re.fullmatch(r"files/[0-9]+/[0-9]+/[0-9]+", entry.filename):
                    raise KaceError("Unexpected archive path; use the KACE export tool")
            self.manifest = Manifest.model_validate_json(self.read("manifest.json"))
            url = urlsplit(self.manifest.appliance)
            if self.manifest.format != FORMAT or url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
                raise KaceError("Not a supported KACE export manifest")
            self.source = "kace-" + hashlib.sha256((self.manifest.appliance.rstrip("/").lower() + "\0" + self.manifest.organization.strip().casefold()).encode()).hexdigest()[:20]
            lines = [line for line in self.read("tickets.jsonl", MAX_UNPACKED).splitlines() if line.strip()]
            if len(lines) != self.manifest.tickets:
                raise KaceError("Ticket count mismatch")
            self.rows = []
            related = 0
            for line in lines:
                row = Row.model_validate_json(line)
                related += len(row.notes) + len(row.work) + sum(len(note.files) for note in row.notes)
                if related > 20000:
                    raise KaceError("Too many related records; export a smaller batch")
                self.rows.append(row)
            if len({row.id for row in self.rows}) != len(self.rows):
                raise KaceError("Ticket count mismatch or duplicate ticket IDs")
            for row in self.rows:
                for values, name in ((row.notes, "change"), (row.work, "work")):
                    if len({value.id for value in values}) != len(values):
                        raise KaceError(f"Ticket {row.id}: duplicate {name} IDs")
                for note in row.notes:
                    if len({file.id for file in note.files}) != len(note.files):
                        raise KaceError(f"Ticket {row.id}: duplicate attachment IDs")
                    for file in note.files:
                        if file.path != f"files/{row.id}/{note.id}/{file.id}" or file.path not in names:
                            raise KaceError(f"Ticket {row.id}: missing or mismatched attachment")
                        if hashlib.sha256(self.read(file.path)).hexdigest() != file.sha256:
                            raise KaceError(f"Ticket {row.id}: attachment checksum mismatch")
                if any(len(key) > 100 or len(value) > 4000 for key, value in row.custom_fields.items()):
                    raise KaceError("Custom field names/values are too long")
            self.unpacked = 0
        except (zipfile.BadZipFile, zlib.error, KeyError, ValidationError, ValueError, UnicodeError, RuntimeError, NotImplementedError, EOFError) as exc:
            if isinstance(exc, KaceError):
                raise
            raise KaceError("Invalid KACE export bundle; check format, fields and dates with the export tool") from None

    def read(self, name: str, limit: int = MAX_FILE) -> bytes:
        with self.zip.open(name) as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise KaceError("Encrypted archives and entries above 25 MiB are not supported")
        self.unpacked += len(data)
        if self.unpacked > MAX_UNPACKED:
            raise KaceError("Bundle expands beyond 256 MiB; export a smaller batch")
        return data


def fingerprint(data: bytes, queue_id: int) -> str:
    return hashlib.sha256(data + b"\0" + str(queue_id).encode()).hexdigest()


def run(session, bundle: Bundle, queue_id: int, *, apply: bool = False) -> dict:
    """An all-or-nothing import, or the same database work rolled back without saving files."""
    saved = []
    counts = Counter()
    warnings = []
    try:
        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(82638149)")):
            raise KaceError("Another KACE import is running; wait for it to finish")
        queue = session.get(Queue, queue_id)
        if queue is None or not queue.is_active:
            raise KaceError("Choose an existing active target queue")
        queue_name = queue.name
        refs = {(kind, sid): tid for kind, sid, tid in session.execute(select(ImportRef.kind, ImportRef.source_id, ImportRef.target_id).where(ImportRef.source == bundle.source))}
        people = {}
        owners = {row.owner.id for row in bundle.rows if row.owner}

        def remember(kind, sid, target):
            refs[(kind, sid)] = str(target)
            session.add(ImportRef(source=bundle.source, kind=kind, source_id=sid, target_id=str(target)))

        def person(value: Person | None, staff=False):
            pid = value.id if value else "0"
            if pid in people:
                return people[pid]
            existing = session.get(User, int(refs[("person", pid)])) if ("person", pid) in refs else None
            if ("person", pid) in refs and existing is None:
                raise KaceError("A previously imported person was removed; review before reimporting")
            email = value.email.strip().lower() if value else ""
            if existing is None and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                existing = session.scalar(select(User).where(func.lower(User.email) == email)) or session.scalar(select(User).join(UserEmail, UserEmail.user_id == User.id).where(func.lower(UserEmail.email) == email))
            if existing is None:
                address = email if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) else f"{bundle.source}-{pid}@import.invalid"
                name = (value.name or email or f"KACE user {pid}") if value else "KACE import"
                existing = User(email=address, display_name=name[:200], role=UserRole.agent if pid in owners else UserRole.requester, is_active=False, auth_source=AuthSource.local)
                session.add(existing)
                session.flush()
                counts["people_created_inactive"] += 1
            else:
                counts["people_matched"] += 1
            if ("person", pid) not in refs:
                remember("person", pid, existing.id)
            people[pid] = existing
            return existing

        for row in bundle.rows:
            old = refs.get(("ticket", row.id))
            ticket = session.get(Ticket, int(old)) if old else None
            if old and ticket is None:
                raise KaceError(f"Ticket {row.id}: its previous Layer 8 record was removed; resolve before reimporting")
            new = ticket is None
            requester = person(row.requester)
            owner = person(row.owner, True) if row.owner else None
            if ticket is None:
                ticket = Ticket(source=TicketSource.api, created_at=row.created_at or datetime.now(UTC))
            if row.created_at:
                ticket.created_at = row.created_at
            if row.created_at is None and len(warnings) < 100:
                warnings.append(f"Ticket {row.id}: no source creation timestamp; import time used for new records")
            if any(note.created_at is None for note in [*row.notes,*row.work]) and len(warnings) < 100:
                warnings.append(f"Ticket {row.id}: some comments/work lack source timestamps; ticket creation date used")
            ticket.subject = row.title
            ticket.description = (plain_text(row.description) + f"\n\n(KACE ticket {row.id})").strip()
            ticket.queue_id, ticket.requester_id = queue.id, requester.id
            ticket.assignee_id = owner.id if owner and owner.role != UserRole.requester else None
            if owner and owner.role == UserRole.requester and len(warnings) < 100:
                warnings.append(f"Ticket {row.id}: owner matched a requester account; left unassigned without granting staff access")
            ticket.status, ticket.priority = row.status, row.priority
            ticket.category = row.category or None
            ticket.closed_at = row.closed_at if row.status == TicketStatus.closed else None
            ticket.resolved_at = row.closed_at if row.status in (TicketStatus.closed, TicketStatus.resolved) else None
            ticket.imported_fields = {"KACE ticket": row.id, "KACE organization": bundle.manifest.organization, "KACE queue": row.source_queue, **{f"KACE {key}": plain_text(value) for key, value in row.custom_fields.items()}}
            session.add(ticket)
            session.flush()
            if new:
                remember("ticket", row.id, ticket.id)
                session.add(TicketEvent(ticket_id=ticket.id, type=EventType.created, actor_id=None, data={"imported_from":"KACE", "number":row.id}, created_at=ticket.created_at))
            counts["tickets_created" if new else "tickets_updated"] += 1
            counts[f"status_{row.status.value}"] += 1
            counts[f"priority_{row.priority.value}"] += 1
            for note in row.notes:
                sid = f"{row.id}:{note.id}"
                author = person(note.author, True)
                cid = refs.get(("comment", sid))
                comment = session.get(Comment, int(cid)) if cid else None
                if cid and comment is None:
                    raise KaceError(f"Ticket {row.id}: a previously imported comment was removed")
                is_new = comment is None
                if comment is None:
                    comment = Comment(ticket_id=ticket.id, author_id=author.id, source=TicketSource.api, created_at=note.created_at or ticket.created_at, is_internal=note.internal)
                comment.body = plain_text(note.body) or "KACE attachment"
                comment.is_internal = comment.is_internal or note.internal
                if note.created_at:
                    comment.created_at = note.created_at
                session.add(comment)
                session.flush()
                if is_new:
                    remember("comment", sid, comment.id)
                counts["comments_created" if is_new else "comments_updated"] += 1
                counts["internal_comments" if comment.is_internal else "public_comments"] += 1
                for file in note.files:
                    aid = f"{sid}:{file.id}"
                    if ("attachment", aid) in refs:
                        previous = session.get(Attachment, UUID(refs[("attachment", aid)]))
                        if previous is None or previous.sha256 != file.sha256:
                            raise KaceError(f"Ticket {row.id}: a previously imported attachment changed or was removed; review before reimporting")
                        counts["attachments_existing"] += 1
                        continue
                    if apply:
                        stored = storage.save(bundle.read(file.path))
                        saved.append(stored.key)
                        name = re.sub(r"[\x00-\x1f\x7f]", "", PurePosixPath(file.name.replace("\\", "/")).name) or "attachment"
                        attachment = Attachment(ticket_id=ticket.id, comment_id=comment.id, uploaded_by_id=author.id, filename=name, content_type=mimetypes.guess_type(name)[0] or "application/octet-stream", size_bytes=stored.size, sha256=stored.sha256, storage_key=stored.key, scan_status=ScanStatus.skipped, created_at=comment.created_at)
                        session.add(attachment)
                        session.flush()
                        remember("attachment", aid, attachment.id)
                    counts["attachments_created"] += 1
            for work in row.work:
                sid = f"{row.id}:{work.id}"
                previous = refs.get(("work", sid))
                author = person(work.author, True)
                entry = session.get(Comment, int(previous)) if previous else None
                if previous and entry is None:
                    raise KaceError(f"Ticket {row.id}: a previously imported work note was removed")
                if entry is None:
                    entry = Comment(ticket_id=ticket.id, author_id=author.id, source=TicketSource.api, is_internal=True, created_at=work.created_at or ticket.created_at)
                entry.body = "KACE work: " + (plain_text(work.note) or "No work note")
                entry.is_internal = True
                session.add(entry)
                session.flush()
                if not previous:
                    remember("work", sid, entry.id)
                counts["work_updated" if previous else "work_created"] += 1
                if work.minutes:
                    time = session.scalar(select(TimeEntry).where(TimeEntry.comment_id == entry.id))
                    if time is None:
                        time = TimeEntry(ticket_id=ticket.id,user_id=author.id,comment_id=entry.id,worked_on=(work.created_at or ticket.created_at).date())
                        counts["time_entries_created"] += 1
                    time.minutes, time.note = work.minutes, plain_text(work.note)[:500]
                    session.add(time)
                elif len(warnings) < 100:
                    warnings.append(f"Ticket {row.id}, work {work.id}: no duration supplied; work note retained without invented time")
        session.flush()
        result = {"applied":apply, "queue":queue_name, "counts":dict(counts), "warnings":warnings}
        if apply:
            session.commit()
        else:
            session.rollback()
        return result
    except Exception:
        session.rollback()
        for key in saved:
            storage.delete(key)
        raise
