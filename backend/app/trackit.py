"""Import from Track-It! (Ivanti/BMC): reads an export bundle made on the Track-It! server by
tools/trackit-export.ps1 and brings its history into Layer 8.

The bundle is a zip: manifest.json, tables/<TRACKIT TABLE>.jsonl (one JSON object per row,
column names as in Track-It! with any trailing ":" dropped; password columns never exported) and
files/<attachment SEQUENCE> (attachment contents, which Track-It! keeps in the database).

What comes across:
- requesters (_CUSTOMER_) and technicians (_PERSONNEL_): matched to existing users by email or
  Windows login; otherwise added (requesters with no password, technicians as inactive accounts
  so their names stay on the history). Nobody's Track-It! password comes across.
- tickets (_TELMASTE_) with their notes (_TELDETAI_): descriptions, technician notes (private
  ones as internal notes), resolutions; system notes, when exported, as one internal summary.
  Status, priority, category, dates, requester and assignee. Each keeps its Track-It! number in
  its description ("Track-It! ticket 12345") so it can be searched for.
- work orders (_WORKORD_, _WORKDET_) as child tickets of their ticket.
- attachments (_ATTACH_) on their tickets.
- solutions (_SOLUTION_) as knowledge base articles (private ones for staff only).
- assets (_INVENTORY_) merged into devices by name or serial (empty fields filled), others added.

Nothing is emailed, no rules or SLA timers run. Every record is remembered in import_refs, so
running a newer bundle later (cut-over) updates tickets and adds only what's new. Run it as
`python -m app.cli import-trackit bundle.zip` (a dry run) and add `--apply` to import.
"""

import html
import json
import mimetypes
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import storage
from app.models import (
    Attachment,
    AuthSource,
    Comment,
    Device,
    DeviceSource,
    EventType,
    FieldList,
    FieldOption,
    ImportRef,
    KbArticle,
    KbArticleVersion,
    KbAudience,
    KbStatus,
    Priority,
    Queue,
    ScanStatus,
    Ticket,
    TicketDevice,
    TicketEvent,
    TicketSource,
    TicketStatus,
    User,
    UserRole,
)

SOURCE = "trackit"
FORMAT = "layer8-trackit-export"
# Columns that must never be read, even if an export carries them.
SECRET = re.compile(r"(PWD|PASSWORD|SALT|^SID$|LOGININFO|TOKEN)", re.I)
SYSTEM_NOTE_LINES = 200
# Track-It! ticket fields Layer 8 has no field for. Kept, under Track-It!'s own names unless
# renamed, as "imported details" on the imported tickets only: no form field, list or choice is
# added anywhere else. Each can be renamed or skipped (--label, --skip, or --interactive).
EXTRA = {
    "HD_LOOKUP01": "Custom Look Up 1", "HD_LOOKUP02": "Custom Look Up 2", "HD_LOOKUP03": "Custom Look Up 3", "HD_LOOKUP04": "Custom Look Up 4",
    "HD_LOOKUP05": "Custom Look Up 5", "HD_LOOKUP06": "Custom Look Up 6", "HD_LOOKUP07": "Custom Look Up 7", "HD_LOOKUP08": "Custom Look Up 8",
    "HD_CCTXT01": "Custom Text 1", "HD_CCTXT02": "Custom Text 2", "HD_CCTXT03": "Custom Text 3", "HD_CCTXT04": "Custom Text 4",
    "HD_CCTXT05": "Custom Text 5", "HD_CCTXT06": "Custom Text 6", "HD_CCDT01": "Custom Date 1", "HD_CCDT02": "Custom Date 2",
    "WO_LOOKUP01": "Custom Look Up 1", "WO_LOOKUP02": "Custom Look Up 2", "WO_LOOKUP03": "Custom Look Up 3", "WO_LOOKUP04": "Custom Look Up 4",
    "WO_CCTXT01": "Custom Text 1", "WO_CCTXT02": "Custom Text 2",
}


class ImportError_(ValueError):
    """The message is safe to show."""


# Reading the bundle


class Bundle:
    def __init__(self, path: str):
        try:
            self.zip = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ImportError_(f"Not a Track-It! export bundle: {exc}") from None
        try:
            self.manifest = json.loads(self.zip.read("manifest.json"))
        except KeyError:
            raise ImportError_("Not a Track-It! export bundle: no manifest.json") from None
        if self.manifest.get("format") != FORMAT:
            raise ImportError_(f"Not a Track-It! export bundle (format {self.manifest.get('format')!r})")
        self.names = set(self.zip.namelist())

    def rows(self, table: str) -> Iterator[dict]:
        name = f"tables/{table}.jsonl"
        if name not in self.names:
            return
        with self.zip.open(name) as f:
            for line in f:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    yield {k.rstrip(":"): v for k, v in row.items() if not SECRET.search(k)}

    def file(self, seq) -> bytes | None:
        name = f"files/{seq}"
        return self.zip.read(name) if name in self.names else None


# Text


class _Text(HTMLParser):
    BLOCK = {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "td":
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK and tag != "li":
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def text(value) -> str:
    """Track-It! notes are HTML (or plain text): as plain text, paragraphs kept."""
    if not value:
        return ""
    s = str(value)
    if re.search(r"<(p|div|br|span|table|li|font|b|i|html)\b", s, re.I):
        parser = _Text()
        parser.feed(s)
        parser.close()
        s = "".join(parser.out)
    else:
        s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def when(value) -> datetime | None:
    """Exported as ISO text in the Track-It! server's local time; kept as given, as UTC-naive becomes aware."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def day(value) -> date | None:
    d = when(value)
    return d.date() if d else None


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _login(value) -> str:
    """DOMAIN\\jsmith or jsmith@corp.example -> jsmith."""
    v = str(value or "").strip().lower()
    return v.split("\\")[-1].split("@")[0]


# What Track-It! users type when there's nothing to say.
JUNK = {"", "-", "--", ".", "n/a", "na", "none", "null", "0", "?", "tbd", "unknown", "not applicable"}


def clean(value) -> str | None:
    """Trimmed, spaces collapsed; placeholders ("N/A", "-", "none") are nothing."""
    if value is None:
        return None
    v = re.sub(r"\s+", " ", str(value)).strip()
    return None if v.lower() in JUNK else v


# Fields found in a bundle that aren't plain ticket fields: column -> (Track-It! name, what it becomes).
SPECIAL = {
    "DEPT": ("Department", "the ticket's department (matched to your existing choices)"),
    "LOCATION": ("Location", "an imported detail"),
    "CONTACT_TYPE": ("Ticket Source", "the ticket's source (email, portal, technician) and an imported detail"),
    "OPENED_GROUP": ("Opened Group", "the queue (a group named like a queue, or --group)"),
}


def detect(bundle: "Bundle") -> list[dict]:
    """Track-It! ticket fields with data in the bundle: how full, how varied, the commonest values."""
    values: dict[str, Counter] = {c: Counter() for c in [*SPECIAL, *[k for k in EXTRA if k.startswith("HD_")]]}
    total = 0
    depts = {r.get("SEQUENCE"): clean(r.get("NAME") or r.get("DEPT")) for r in bundle.rows("_DEPART_")}
    locs = {r.get("SEQUENCE"): clean(r.get("NAME") or r.get("CODE")) for r in bundle.rows("_LOCATION_")}
    for row in bundle.rows("_TELMASTE_"):
        total += 1
        for col in values:
            v = row.get(col)
            v = depts.get(v) if col == "DEPT" else locs.get(v) if col == "LOCATION" else (str(v)[:10] if v and "CCDT" in col else clean(v))
            if v:
                values[col][v] += 1
    out = []
    for col, counts in values.items():
        used = sum(counts.values())
        if not used:
            continue
        name, becomes = SPECIAL.get(col, (EXTRA.get(col, col), "an imported detail"))
        out.append({"column": col, "name": name, "used": used, "total": total, "distinct": len(counts), "top": counts.most_common(5), "becomes": becomes})
    return out


def priority_of(label: str) -> Priority:
    l = (label or "").lower()
    if re.search(r"crit|urgent|emergenc|p1\b|^1\b", l):
        return Priority.critical
    if re.search(r"high|p2\b|^2\b", l):
        return Priority.high
    if re.search(r"low|p4\b|^4\b|^5\b", l):
        return Priority.low
    return Priority.medium


# The import


@dataclass
class Report:
    apply: bool
    counts: Counter = field(default_factory=Counter)
    priorities: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    queues: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)
    attachment_bytes: int = 0

    def problem(self, text: str) -> None:
        if len(self.problems) < 200:
            self.problems.append(text)
        self.counts["problems"] += 1

    def lines(self) -> list[str]:
        c = self.counts
        out = [
            ("Imported" if self.apply else "Dry run (nothing saved)") + ":",
            f"  requesters: {c['requester_matched']} matched, {c['requester_new']} new",
            f"  technicians: {c['tech_matched']} matched, {c['tech_new']} new (inactive)",
            f"  tickets: {c['ticket_new']} new, {c['ticket_updated']} updated; child tickets from work orders: {c['workorder_new']} new, {c['workorder_updated']} updated",
            f"  notes: {c['comment_new']} new ({c['comment_internal']} internal), {c['system_summary']} system-note summaries",
            f"  attachments: {c['attachment_new']} new ({self.attachment_bytes / 1048576:.1f} MB), {c['attachment_missing']} missing from the bundle",
            f"  knowledge base: {c['article_new']} new, {c['article_updated']} updated",
            f"  devices: {c['device_matched']} matched, {c['device_new']} new",
            "  priorities: " + (", ".join(f"{k} -> {v}" for k, v in sorted(self.priorities.items())) or "none"),
            "  statuses: " + (", ".join(f"{k}: {v}" for k, v in sorted(self.statuses.items())) or "none"),
            "  queues (from Track-It!'s opened group): " + (", ".join(f"{k}: {v}" for k, v in sorted(self.queues.items())) or "none"),
            "  ticket sources: " + (", ".join(f"{k}: {v}" for k, v in sorted(self.sources.items())) or "none"),
            f"  departments: {c['department_set']} tickets matched to your department choices, {c['department_unmatched']} kept as an imported detail"
            + (f", {c['department_new']} choices added" if c["department_new"] else "") + f"; devices linked to tickets: {c['device_linked']}",
        ]
        if self.problems:
            out.append(f"  problems ({c['problems']}):")
            out += [f"    {p}" for p in self.problems[:50]]
        return out


class Importer:
    def __init__(
        self, session: Session, bundle: Bundle, apply: bool, queue: Queue | None = None, queue_map: dict[str, Queue] | None = None,
        labels: dict[str, str] | None = None, skip: set[str] | None = None, departments: str = "match",
    ):
        self.s, self.b, self.apply = session, bundle, apply
        self.queue_map = {k.lower(): v.id for k, v in (queue_map or {}).items()}
        self.skip = {c.upper() for c in (skip or set())}
        self.labels = {c: n for c, n in {**EXTRA, "LOCATION": "Location", "CONTACT_TYPE": "Ticket source", **(labels or {})}.items() if c not in self.skip}
        self.department_mode = departments
        self.r = Report(apply=apply)
        self.queue = queue or session.scalar(select(Queue).where(Queue.is_default)) or session.scalar(select(Queue).order_by(Queue.id))
        if self.queue is None:
            raise ImportError_("Add a queue first: imported tickets go into the default queue")
        self.refs: dict[tuple[str, str], str] = {
            (k, sid): tid for k, sid, tid in session.execute(select(ImportRef.kind, ImportRef.source_id, ImportRef.target_id).where(ImportRef.source == SOURCE))
        }
        self.lookup: dict[str, dict] = {}

    # Helpers

    def _ref(self, kind: str, source_id) -> int | None:
        """What a record became (its id), or None if it wasn't imported yet."""
        got = self.refs.get((kind, str(source_id)))
        return int(got) if got is not None and got.isdigit() else (0 if got is not None else None)

    def _seen(self, kind: str, source_id) -> bool:
        return (kind, str(source_id)) in self.refs

    def _remember(self, kind: str, source_id, target_id) -> None:
        self.refs[(kind, str(source_id))] = str(target_id)
        self.s.add(ImportRef(source=SOURCE, kind=kind, source_id=str(source_id), target_id=str(target_id)))

    def _table(self, name: str, key: str = "SEQUENCE") -> dict:
        return {r.get(key): r for r in self.b.rows(name)}

    # People

    def _user_by(self, email: str, login: str) -> User | None:
        if email:
            u = self.s.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if u:
                return u
        if login:
            return self.s.scalar(select(User).where((func.lower(User.sam_account_name) == login) | (func.split_part(func.lower(User.upn), "@", 1) == login)).limit(1))
        return None

    def _person(self, kind: str, row: dict) -> int:
        seq = row.get("SEQUENCE")
        if (got := self._ref(kind, seq)) is not None:
            return got
        email = str(row.get("_EMAILID_") or "").strip()
        email = email if "@" in email and " " not in email else ""
        login = _login(row.get("WINUSERID") or row.get("LDAPUSERID") or row.get("CLIENT") or "")
        u = self._user_by(email, login)
        tech = kind == "technician"
        if u is not None:
            self.r.counts["tech_matched" if tech else "requester_matched"] += 1
        else:
            name = " ".join(str(row.get(k) or "").strip() for k in ("FNAME", "NAME")).strip() or str(row.get("CODE") or row.get("CLIENT") or f"Track-It! {kind} {seq}")
            u = User(
                email=email or f"trackit-{kind}-{seq}@import.invalid", display_name=name[:200], role=UserRole.agent if tech else UserRole.requester,
                is_active=False if tech else not row.get("_INACTIVE_"), auth_source=AuthSource.local, phone=(str(row.get("PHONE") or "")[:64] or None),
                title=(str(row.get("JOB_TITLE") or row.get("POSITION") or "")[:200] or None), sam_account_name=login or None,
            )
            dept = self.lookup.get("departments", {}).get(row.get("DEPT"))
            if dept:
                u.department = str(dept.get("NAME") or dept.get("DEPT") or "")[:200] or None
            self.s.add(u)
            self.s.flush()
            self.r.counts["tech_new" if tech else "requester_new"] += 1
        self._remember(kind, seq, u.id)
        return u.id

    def _requester(self, seq) -> int:
        row = self.lookup["customers"].get(seq)
        if row is None:
            if seq not in (None, 0):
                self.r.problem(f"requester {seq} isn't in the bundle; the ticket goes to the import user")
            return self.fallback.id
        return self._person("requester", row)

    def _technician(self, seq) -> int | None:
        row = self.lookup["personnel"].get(seq)
        return self._person("technician", row) if row else None

    # Tickets

    def _status(self, row: dict) -> TicketStatus:
        if str(row.get("STATUS") or "").upper() == "C":
            return TicketStatus.closed
        ud = self.lookup["statuses"].get(row.get("SEQ_UDSTATUS"))
        label = str((ud or {}).get("DESCRIPTION") or (ud or {}).get("ID") or "")
        return TicketStatus.waiting_on_user if re.search(r"wait|pending|hold", label, re.I) else TicketStatus.open

    def _priority(self, row: dict) -> Priority:
        p = self.lookup["priorities"].get(row.get("SEQ_PRIORITY"))
        label = str((p or {}).get("PRIORITY_ID") or (p or {}).get("DESCRIPTION") or "")
        mapped = priority_of(label)
        self.r.priorities[f"{label or '(none)'} -> {mapped.value}"] += 1
        return mapped

    def _category(self, seq) -> str | None:
        c = self.lookup["categories"].get(seq) or {}
        name = str(c.get("FULLPATH") or c.get("DESCRIPTION") or c.get("CODE") or "").strip()
        return name[:100] or None

    def _queue_for(self, row: dict) -> int:
        group = str(row.get("OPENED_GROUP") or "").strip()
        qid = self.queue_map.get(group.lower()) if group else None
        if qid is None and group:
            qid = self.s.scalar(select(Queue.id).where(func.lower(Queue.name) == group.lower()))
        qid = qid or self.queue.id
        self.r.queues[f"{group or '(none)'} -> {self.s.get(Queue, qid).name}"] += 1
        return qid

    def _source(self, row: dict) -> TicketSource:
        ct = str(row.get("CONTACT_TYPE") or "").strip()
        lc = ct.lower()
        src = TicketSource.email if "mail" in lc or row.get("_EMAILID_") else TicketSource.portal if re.search(r"self|web|portal", lc) else TicketSource.agent
        self.r.sources[f"{ct or '(none)'} -> {src.value}"] += 1
        return src

    def _department_name(self, row: dict) -> str | None:
        dept = self.lookup["departments"].get(row.get("DEPT") or row.get("DEPARTMENT"))
        return (clean((dept or {}).get("NAME") or (dept or {}).get("DEPT")) or "")[:100] or None

    def _department(self, name: str | None) -> int | None:
        """An existing department choice by name; with --departments add, missing ones are added."""
        if not name or self.department_mode == "skip":
            return None
        key = name.lower()
        if key not in self.departments:
            opt = self.s.scalar(select(FieldOption).where(FieldOption.list == FieldList.department, func.lower(FieldOption.label) == key))
            if opt is None and self.department_mode == "add":
                opt = FieldOption(list=FieldList.department, label=name, position=100)
                self.s.add(opt)
                self.s.flush()
                self.r.counts["department_new"] += 1
            self.departments[key] = opt.id if opt else None
        if self.departments[key]:
            self.r.counts["department_set"] += 1
        else:
            self.r.counts["department_unmatched"] += 1
        return self.departments[key]

    def _imported(self, row: dict, department: str | None, matched: bool) -> dict:
        """The Track-It! fields Layer 8 has no place for, under their chosen names."""
        out = {}
        if department and not matched and self.department_mode != "skip" and "DEPT" not in self.skip:
            out["Department"] = department
        for col, label in self.labels.items():
            if col == "LOCATION":
                loc = self.lookup["locations"].get(row.get("LOCATION"))
                v = clean((loc or {}).get("NAME") or (loc or {}).get("CODE"))
            else:
                v = row.get(col)
                v = str(v)[:10] if v and "CCDT" in col else clean(v)
            if v:
                out[label] = v
        return out

    def _ticket(self, kind: str, row: dict, description: str, parent_id: int | None = None) -> Ticket:
        seq = row.get("SEQUENCE")
        status = self._status(row)
        self.r.statuses[status.value] += 1
        opened, closed = when(row.get("DATE OPEN")), when(row.get("CLOSED ON"))
        tid = self._ref(kind, seq)
        t = self.s.get(Ticket, tid) if tid else None
        new = t is None
        if new:
            t = Ticket(source=self._source(row), queue_id=self._queue_for(row), created_at=opened or when(row.get("LASTMODIFIED")))
        t.description = (description + f"\n\n(Track-It! {'ticket' if kind == 'ticket' else 'work order'} {seq})").strip()
        dept = self._department_name(row)
        t.department_id = self._department(dept) if "DEPT" not in self.skip else None
        t.imported_fields = self._imported(row, dept, t.department_id is not None)
        t.resolution_due_at = when(row.get("DUE_DATE"))
        t.subject = (str(row.get("DESCRIPTION") or "").strip() or f"Track-It! {kind} {seq}")[:300]
        t.requester_id = self._requester(row.get("CLIENT"))
        t.assignee_id = self._technician(row.get("SENT TO") or row.get("RESPONDED BY"))
        t.priority, t.category, t.parent_id = self._priority(row), self._category(row.get("CATEGORY")), parent_id or t.parent_id
        t.status, t.status_changed_at = status, closed if status == TicketStatus.closed else t.status_changed_at
        t.closed_at = t.resolved_at = closed if status == TicketStatus.closed else None
        t.contact_phone = (str(row.get("PHONE") or "")[:64] or None) if new else t.contact_phone
        t.first_responded_at = when(row.get("RESP_DATE")) or when(row.get("RESPONSE_DATE"))
        if new:
            self.s.add(t)
            self.s.flush()
            device = self._ref("device", row.get("SEQ_INVITEM")) if row.get("SEQ_INVITEM") else None
            if device:
                self.s.add(TicketDevice(ticket_id=t.id, device_id=device, linked_at=t.created_at))
                self.r.counts["device_linked"] += 1
            self.s.add(TicketEvent(ticket_id=t.id, type=EventType.created, actor_id=None, data={"imported_from": "Track-It!", "number": seq}, created_at=t.created_at))
            self._remember(kind, seq, t.id)
        t.updated_at = when(row.get("LASTMODIFIED")) or t.updated_at
        self.r.counts[f"{kind}_{'new' if new else 'updated'}"] += 1
        return t

    def _notes(self, t: Ticket, notes: list[dict], requester_id: int, kind_ref: str = "note") -> None:
        system: list[str] = []
        for n in sorted(notes, key=lambda n: (str(n.get("PDATE") or ""), n.get("SEQUENCE") or 0)):
            kind = str(n.get("NOTETYPE") or "")
            body = text(n.get("NOTE"))
            if kind == "Ticket Description" or not body:
                continue
            if kind == "System Note":
                system.append(f"{(n.get('PDATE') or '')[:16].replace('T', ' ')}  {body.splitlines()[0][:300]}")
                continue
            if self._seen(kind_ref, n.get("SEQUENCE")):
                continue
            by_requester = bool(n.get("REQUESTOR#"))
            author = requester_id if by_requester else (self._technician(n.get("PERSON")) or self.fallback.id)
            internal = bool(n.get("ISPRIVATE"))
            if kind == "Ticket Resolution":
                body = "Resolution:\n" + body
            spent = n.get("TIME SPENT")
            if isinstance(spent, (int, float)) and spent > 0:
                # Track-It! keeps time spent in seconds.
                mins = round(spent / 60)
                body += f"\n\n(Time spent: {mins // 60} h {mins % 60} min)" if mins >= 60 else f"\n\n(Time spent: {max(mins, 1)} min)"
            c = Comment(ticket_id=t.id, author_id=author, body=body, is_internal=internal, source=TicketSource.email if n.get("EMAILTOEMAILFROM") else TicketSource.agent, created_at=when(n.get("PDATE")) or t.created_at)
            self.s.add(c)
            self.s.flush()
            self._remember(kind_ref, n.get("SEQUENCE"), c.id)
            self.r.counts["comment_new"] += 1
            self.r.counts["comment_internal"] += internal
        if system and not self._seen("system_notes", t.id):
            more = f"\n… and {len(system) - SYSTEM_NOTE_LINES} more" if len(system) > SYSTEM_NOTE_LINES else ""
            c = Comment(ticket_id=t.id, author_id=self.fallback.id, body="Track-It! history (system notes):\n" + "\n".join(system[:SYSTEM_NOTE_LINES]) + more, is_internal=True, source=TicketSource.agent, created_at=t.created_at)
            self.s.add(c)
            self.s.flush()
            self._remember("system_notes", t.id, c.id)
            self.r.counts["system_summary"] += 1

    def _attachments(self, t: Ticket, rows: list[dict]) -> None:
        for a in rows:
            seq = a.get("SEQUENCE")
            if self._seen("attachment", seq):
                continue
            data = self.b.file(seq)
            if data is None:
                self.r.counts["attachment_missing"] += 1
                continue
            name = (str(a.get("FILENAME") or f"attachment-{seq}").replace("\\", "/").split("/")[-1] or f"attachment-{seq}")[:255]
            self.r.attachment_bytes += len(data)
            self.r.counts["attachment_new"] += 1
            if not self.apply:
                continue
            stored = storage.save(data)
            self.saved.append(stored.key)
            att = Attachment(
                ticket_id=t.id, filename=name, content_type=mimetypes.guess_type(name)[0] or "application/octet-stream", size_bytes=stored.size,
                sha256=stored.sha256, storage_key=stored.key, scan_status=ScanStatus.skipped, created_at=when(a.get("FILEDATE")) or t.created_at,
            )
            self.s.add(att)
            self.s.flush()
            self._remember("attachment", seq, att.id)

    # Knowledge base and devices

    def _articles(self) -> None:
        topics = self._table("_TOPIC_")
        for row in self.b.rows("_SOLUTION_"):
            seq = row.get("SEQUENCE")
            title = (str(row.get("SUMMARY") or "").strip() or f"Track-It! solution {seq}")[:300]
            body = text(row.get("SOLUTION")) + f"\n\n(Track-It! solution {seq})"
            category = str((topics.get(row.get("SEQ_TOPIC")) or {}).get("NAME") or "").strip()[:100] or None
            aid = self._ref("article", seq)
            a = self.s.get(KbArticle, aid) if aid else None
            if a is None:
                a = KbArticle(title=title, body=body, category=category, created_at=when(row.get("LASTMODIFIED")))
                self.s.add(a)
                new = True
            else:
                new = False
                a.title, a.body, a.category = title, body, category
            a.audience = KbAudience.staff if row.get("ISPRIVATE") else KbAudience.everyone
            a.status = KbStatus.archived if row.get("_INACTIVE_") else KbStatus.published
            a.published_at = a.published_at or when(row.get("LASTMODIFIED"))
            self.s.flush()
            if new:
                self.s.add(KbArticleVersion(article_id=a.id, number=1, title=a.title, body=a.body, keywords=a.keywords or "", note="Imported from Track-It!"))
                self._remember("article", seq, a.id)
            self.r.counts[f"article_{'new' if new else 'updated'}"] += 1

    def _devices(self) -> None:
        for row in self.b.rows("_INVENTORY_"):
            seq = row.get("SEQUENCE")
            name = str(row.get("NAME") or "").strip()
            serial = str(row.get("SERIAL_NUMBER") or row.get("COMPUTER_SERVICE_TAG") or "").strip()
            if not name and not serial:
                continue
            did = self._ref("device", seq)
            d = self.s.get(Device, did) if did else None
            if d is None and name:
                d = self.s.scalar(select(Device).where(func.lower(Device.name) == name.lower().split(".")[0]).limit(1))
            if d is None and serial:
                d = self.s.scalar(select(Device).where(func.lower(Device.serial) == serial.lower()).limit(1))
            if d is None:
                d = Device(name=(name or serial)[:255], source=DeviceSource.manual, is_active=not row.get("_INACTIVE_"), notes="Imported from Track-It!")
                self.s.add(d)
                self.r.counts["device_new"] += 1
            elif did is None:
                self.r.counts["device_matched"] += 1
            # Fill gaps only.
            d.manufacturer = d.manufacturer or str(row.get("COMPUTER_MANUFACTURER") or "")[:200]
            d.serial = d.serial or serial[:200]
            d.purchased_on = d.purchased_on or day(row.get("PUR_DATE"))
            d.warranty_ends = d.warranty_ends or day(row.get("WARR_EXP_DATE"))
            if d.source == DeviceSource.manual and not d.os and row.get("DOS_VER"):
                d.os = str(row.get("DOS_VER"))[:255]
            self.s.flush()
            if did is None:
                self._remember("device", seq, d.id)

    # Run

    def run(self) -> Report:
        self.saved: list[str] = []
        self.fallback = self.s.scalar(select(User).where(func.lower(User.email) == "trackit-import@import.invalid"))
        if self.fallback is None:
            self.fallback = User(email="trackit-import@import.invalid", display_name="Track-It! import", role=UserRole.agent, is_active=False, auth_source=AuthSource.local)
            self.s.add(self.fallback)
            self.s.flush()
        self.lookup = {
            "customers": self._table("_CUSTOMER_"), "personnel": self._table("_PERSONNEL_"), "departments": self._table("_DEPART_"),
            "categories": self._table("_CATEGORIES_"), "priorities": self._table("_PRIORITY_"), "statuses": self._table("_UDSTATUS_"),
            "locations": self._table("_LOCATION_"),
        }
        self.departments: dict[str, int] = {}
        # Devices first, so tickets can be linked to theirs.
        self._devices()
        notes: dict = {}
        for n in self.b.rows("_TELDETAI_"):
            notes.setdefault(n.get("PROBLEM #"), []).append(n)
        attachments: dict = {}
        for a in self.b.rows("_ATTACH_"):
            if a.get("SEQ_INCIDENT"):
                attachments.setdefault(a.get("SEQ_INCIDENT"), []).append(a)
        done = 0
        parents: list[tuple[int, object]] = []
        for row in self.b.rows("_TELMASTE_"):
            mine = notes.get(row.get("SEQUENCE"), [])
            desc = next((text(n.get("NOTE")) for n in sorted(mine, key=lambda n: str(n.get("PDATE") or "")) if n.get("NOTETYPE") == "Ticket Description"), "") or text(row.get("NOTE"))
            t = self._ticket("ticket", row, desc)
            if row.get("PARENT"):
                parents.append((t.id, row["PARENT"]))
            self._notes(t, mine, t.requester_id)
            self._attachments(t, attachments.get(row.get("SEQUENCE"), []))
            done += 1
            if done % 500 == 0:
                self.s.flush()
                self.s.expunge_all()
                self.fallback = self.s.merge(self.fallback)
                self.queue = self.s.merge(self.queue)
        for tid, parent in parents:
            pid = self._ref("ticket", parent)
            if pid and pid != tid:
                self.s.get(Ticket, tid).parent_id = pid
            elif not pid:
                self.r.problem(f"ticket {tid}: its Track-It! parent {parent} isn't imported")
        wo_notes: dict = {}
        for n in self.b.rows("_WORKDET_"):
            wo_notes.setdefault(n.get("TTNUM"), []).append(n)
        for row in self.b.rows("_WORKORD_"):
            parent = self._ref("ticket", row.get("HDNUM"))
            if parent is None:
                self.r.problem(f"work order {row.get('SEQUENCE')}: its ticket {row.get('HDNUM')} isn't imported; skipped")
                continue
            t = self._ticket("workorder", row, text(row.get("NOTE")), parent_id=parent)
            self._notes(t, wo_notes.get(row.get("SEQUENCE"), []), t.requester_id, kind_ref="workorder_note")
        self._articles()
        self.s.flush()
        return self.r


def _queue(session: Session, name: str) -> Queue:
    q = session.scalar(select(Queue).where(func.lower(Queue.name) == name.strip().lower()))
    if q is None:
        raise ImportError_(f"No queue called {name.strip()!r}")
    return q


def run(
    session: Session, path: str, apply: bool = False, queue_name: str | None = None, queue_map: dict[str, str] | None = None,
    labels: dict[str, str] | None = None, skip: set[str] | None = None, departments: str = "match",
) -> Report:
    """Import a bundle. A dry run rolls everything back (and saves no files). Commits when applying.
    queue_map: Track-It! opened group -> queue name (groups named like a queue go there anyway).
    labels: names for Track-It! fields kept as imported details ({"HD_LOOKUP01": "Building"}).
    skip: Track-It! fields to leave out. departments: match (existing choices only), add, or skip."""
    bundle = Bundle(path)
    queue = _queue(session, queue_name) if queue_name else None
    qmap = {group: _queue(session, name) for group, name in (queue_map or {}).items()}
    known = {*EXTRA, "LOCATION", "CONTACT_TYPE", "DEPT"}
    unknown = [k for k in [*(labels or {}), *(skip or set())] if k.upper() not in known]
    if unknown:
        raise ImportError_(f"Unknown Track-It! field(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    if departments not in ("match", "add", "skip"):
        raise ImportError_("departments: match, add or skip")
    imp = Importer(session, bundle, apply, queue, qmap, labels, skip, departments)
    try:
        report = imp.run()
    except Exception:
        session.rollback()
        for key in getattr(imp, "saved", []):
            storage.delete(key)
        raise
    if apply:
        session.commit()
    else:
        session.rollback()
    return report
