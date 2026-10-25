# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import ticket history from a Spiceworks Cloud Help Desk CSV export (Tickets > Export).

Spiceworks' "full" export is one row per ticket: number, summary, description, people by name (or
email), category, department, priority, status, dates and time spent. It has no comments or
attachments, so those stay in Spiceworks. Each ticket's description ends with
"(Spiceworks ticket 123)" so searching the old number finds it.

Like the Track-It! import: a dry run unless applying, nothing is emailed, rules, SLA timers and
surveys don't run, and every ticket is remembered (import_refs), so a later export only updates.
People are matched to existing accounts by email, else by a display name only one account has;
the rest are added (requesters with no password, technicians inactive).
"""
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuthSource, EventType, FieldList, FieldOption, ImportRef, Priority, Queue, Ticket, TicketEvent, TicketSource,
    TicketStatus, TimeEntry, User, UserEmail, UserRole,
)
from app.trackit import ImportError_, clean

SOURCE = "spiceworks"
REQUIRED = {"Ticket Number", "Summary", "Description", "Created On", "Status"}
# Spiceworks' export cuts long descriptions here.
DESCRIPTION_LIMIT = 10000
PRIORITY = {"high": Priority.high, "medium": Priority.medium, "low": Priority.low}


def when(value) -> datetime | None:
    """"1/2/2019 3:04 pm UTC" (the export's format)."""
    v = re.sub(r"\s+UTC$", "", str(value or "").strip(), flags=re.I)
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


@dataclass
class Report:
    apply: bool
    counts: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)

    def problem(self, text: str) -> None:
        if len(self.problems) < 200:
            self.problems.append(text)
        self.counts["problems"] += 1

    def lines(self) -> list[str]:
        c = self.counts
        out = [
            ("Imported" if self.apply else "Dry run (nothing saved)") + ":",
            f"  tickets: {c['ticket_new']} new, {c['ticket_updated']} updated ({c['open']} open, {c['closed']} closed)",
            f"  requesters: {c['requester_matched']} matched, {c['requester_new']} new",
            f"  technicians: {c['tech_matched']} matched, {c['tech_new']} new (inactive); {c['unassigned']} tickets unassigned",
            f"  departments: {c['department_set']} tickets matched to your department choices, {c['department_unmatched']} kept as an imported detail"
            + (f", {c['department_new']} choices added" if c["department_new"] else ""),
            f"  time logged: {c['time_entries']} entries, {c['time_minutes']} minutes",
            f"  descriptions Spiceworks cut at {DESCRIPTION_LIMIT:,} characters: {c['truncated']}",
            "  comments and attachments: not in Spiceworks' export; they stay in Spiceworks",
        ]
        if self.problems:
            out.append(f"  problems ({c['problems']}):")
            out += [f"    {p}" for p in self.problems[:50]]
        return out


class Importer:
    def __init__(self, session: Session, rows: list[dict], apply: bool, queue: Queue, departments: str):
        self.s, self.rows, self.queue, self.department_mode = session, rows, queue, departments
        self.r = Report(apply)
        self.refs = {(kind, sid): tid for kind, sid, tid in session.execute(select(ImportRef.kind, ImportRef.source_id, ImportRef.target_id).where(ImportRef.source == SOURCE))}
        self.people: dict[tuple[str, str], int] = {}
        self.departments: dict[str, int | None] = {}

    def _remember(self, kind: str, source_id: str, target_id: int) -> None:
        self.refs[(kind, source_id)] = str(target_id)
        self.s.add(ImportRef(source=SOURCE, kind=kind, source_id=source_id, target_id=str(target_id)))

    def _person(self, value: str, tech: bool) -> int | None:
        """An existing account by email or unique display name; else a new requester / inactive technician."""
        value = clean(value) or ""
        if not value:
            return None
        key = ("technician" if tech else "requester", value.lower())
        if key in self.people:
            return self.people[key]
        email = value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""
        user = None
        if email:
            user = self.s.scalar(select(User).where(func.lower(User.email) == email.lower())) or self.s.scalar(
                select(User).join(UserEmail, UserEmail.user_id == User.id).where(func.lower(UserEmail.email) == email.lower()))
        else:
            named = self.s.scalars(select(User).where(func.lower(User.display_name) == value.lower()).limit(2)).all()
            user = named[0] if len(named) == 1 else None
        if user is not None:
            self.r.counts["tech_matched" if tech else "requester_matched"] += 1
        else:
            if (ref := self.refs.get(("person", key[0] + ":" + value.lower()))) is not None:
                self.people[key] = int(ref)
                return self.people[key]
            slug = re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")[:60] or "someone"
            address = email or f"spiceworks-{key[0]}-{slug}@import.invalid"
            n = 1
            while self.s.scalar(select(User.id).where(func.lower(User.email) == address.lower())):
                n += 1
                address = f"spiceworks-{key[0]}-{slug}-{n}@import.invalid"
            user = User(email=address, display_name=(value.split("@")[0] if email else value)[:200], role=UserRole.agent if tech else UserRole.requester,
                        is_active=not tech, auth_source=AuthSource.local)
            self.s.add(user)
            self.s.flush()
            self._remember("person", key[0] + ":" + value.lower(), user.id)
            self.r.counts["tech_new" if tech else "requester_new"] += 1
        self.people[key] = user.id
        return user.id

    def _department(self, name: str | None) -> int | None:
        if not name or self.department_mode == "skip":
            return None
        key = name.lower()
        if key not in self.departments:
            opt = self.s.scalar(select(FieldOption).where(FieldOption.list == FieldList.department, func.lower(FieldOption.label) == key))
            if opt is None and self.department_mode == "add":
                opt = FieldOption(list=FieldList.department, label=name[:100], position=100)
                self.s.add(opt)
                self.s.flush()
                self.r.counts["department_new"] += 1
            self.departments[key] = opt.id if opt else None
        self.r.counts["department_set" if self.departments[key] else "department_unmatched"] += 1
        return self.departments[key]

    def _ticket(self, row: dict) -> None:
        number = str(row.get("Ticket Number") or "").strip()
        if not number.isdigit():
            self.r.problem(f"a row has no ticket number ({(row.get('Summary') or '')[:40]!r}); skipped")
            return
        tid = self.refs.get(("ticket", number))
        t = self.s.get(Ticket, int(tid)) if tid else None
        new = t is None
        created, closed = when(row.get("Created On")), when(row.get("Closed On"))
        if created is None:
            self.r.problem(f"ticket {number}: no readable Created On ({row.get('Created On')!r}); used the import time")
        creator = clean(row.get("Created By")) or ""
        if new:
            t = Ticket(queue_id=self.queue.id, created_at=created or datetime.now(UTC),
                       source=TicketSource.email if "@" in creator else TicketSource.portal)
        raw = str(row.get("Description") or "")
        cut = len(raw) >= DESCRIPTION_LIMIT
        description = raw.replace("\r\n", "\n").strip()
        self.r.counts["truncated"] += cut
        t.subject = (str(row.get("Summary") or "").strip() or f"Spiceworks ticket {number}")[:300]
        t.description = (description + ("\n\n(Spiceworks' export cut this description off here.)" if cut else "") + f"\n\n(Spiceworks ticket {number})").strip()
        t.requester_id = self._person(creator, tech=False) or t.requester_id or self._fallback()
        t.assignee_id = self._person(row.get("Assigned To") or "", tech=True)
        self.r.counts["unassigned"] += t.assignee_id is None
        t.priority = PRIORITY.get(str(row.get("Priority") or "").strip().lower(), Priority.medium)
        t.category = (clean(row.get("Category")) or "")[:100] or None
        department = clean(row.get("Department"))
        t.department_id = self._department(department)
        extra = {"Department": department} if department and not t.department_id and self.department_mode != "skip" else {}
        if link := clean(row.get("Link to Ticket")):
            extra["Spiceworks link"] = link
        if vendor := clean(row.get("Vendor Ticket")):
            extra["Vendor ticket"] = vendor
        t.imported_fields = extra
        t.contact_phone = (clean(row.get("Contact Number")) or "")[:64] or t.contact_phone
        t.resolution_due_at = when(row.get("Due On"))
        closed_now = str(row.get("Status") or "").strip().lower() == "closed"
        t.status = TicketStatus.closed if closed_now else TicketStatus.open
        t.closed_at = t.resolved_at = (closed or created) if closed_now else None
        t.status_changed_at = t.closed_at or t.created_at
        t.updated_at = t.closed_at or t.created_at
        self.r.counts["closed" if closed_now else "open"] += 1
        if new:
            self.s.add(t)
            self.s.flush()
            self.s.add(TicketEvent(ticket_id=t.id, type=EventType.created, actor_id=None, data={"imported_from": "Spiceworks", "number": number}, created_at=t.created_at))
            minutes = int(row.get("Time Spent") or 0) if str(row.get("Time Spent") or "").strip().isdigit() else 0
            if minutes > 0:
                self.s.add(TimeEntry(ticket_id=t.id, user_id=t.assignee_id, minutes=minutes, worked_on=(t.closed_at or t.created_at).date(), note="Logged in Spiceworks"))
                self.r.counts["time_entries"] += 1
                self.r.counts["time_minutes"] += minutes
            self._remember("ticket", number, t.id)
        self.r.counts["ticket_new" if new else "ticket_updated"] += 1

    def _fallback(self) -> int:
        user = self.s.scalar(select(User).where(func.lower(User.email) == "spiceworks-import@import.invalid"))
        if user is None:
            user = User(email="spiceworks-import@import.invalid", display_name="Spiceworks import", role=UserRole.agent, is_active=False, auth_source=AuthSource.local)
            self.s.add(user)
            self.s.flush()
        return user.id

    def run(self) -> Report:
        for row in self.rows:
            self._ticket(row)
        self.s.flush()
        return self.r


def read(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    csv.field_size_limit(10_000_000)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ImportError_(f"This isn't a Spiceworks ticket export: no {', '.join(sorted(missing))} column")
    return list(reader)


def run(session: Session, path: str, *, queue_name: str, apply: bool = False, departments: str = "match") -> Report:
    """Import a Spiceworks CSV export into one queue. A dry run rolls everything back."""
    if departments not in ("match", "add", "skip"):
        raise ImportError_("departments: match, add or skip")
    queue = session.scalar(select(Queue).where(func.lower(Queue.name) == queue_name.strip().lower()))
    if queue is None:
        raise ImportError_(f"No queue called {queue_name.strip()!r}; make it first (Settings > Queues)")
    with open(path, "rb") as f:
        rows = read(f.read())
    try:
        report = Importer(session, rows, apply, queue, departments).run()
    except Exception:
        session.rollback()
        raise
    if apply:
        session.commit()
    else:
        session.rollback()
    return report
