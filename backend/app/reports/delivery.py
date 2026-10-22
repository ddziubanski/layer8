"""Reports as files and email: PDF/CSV, sending now, and schedules the worker runs.

Recipients must be people the service desk knows (active users) or addresses in the
"Accept email from" domains, so a report can't be sent just anywhere.
"""

import calendar
import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services, sla, storage
from app.config import settings
from app.models import OutboundEmail, ReportSchedule, SavedReport, ScriptSchedule, User
from app.reports import engine, pdf, presets

log = logging.getLogger(__name__)

# Relative periods for schedules (and the Reports screen's names for them).
PERIODS = {
    "yesterday": "Yesterday",
    "last_7_days": "Last 7 days",
    "last_30_days": "Last 30 days",
    "previous_week": "Previous week (Mon–Sun)",
    "previous_month": "Previous month",
    "this_month": "This month so far",
    "this_year": "This year so far",
    "last_12_months": "Last 12 months",
    "all": "All time",
}
FREQUENCIES = ("once", "daily", "weekly", "monthly")
FORMATS = ("pdf", "csv", "both")


class DeliveryError(ValueError):
    """The message is safe to show."""


def period_range(name: str, today: date) -> tuple[date | None, date | None]:
    if name == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if name == "last_7_days":
        return today - timedelta(days=6), today
    if name == "last_30_days":
        return today - timedelta(days=29), today
    if name == "previous_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if name == "previous_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if name == "this_month":
        return today.replace(day=1), today
    if name == "this_year":
        return today.replace(month=1, day=1), today
    if name == "last_12_months":
        return today.replace(year=today.year - 1) + timedelta(days=1), today
    if name == "all":
        return None, None
    raise DeliveryError(f"Unknown period {name!r}")


# Files


@dataclass
class ReportFile:
    filename: str
    content_type: str
    data: bytes


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "report"


def csv_bytes(result: engine.ReportResult) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c.label for c in result.columns])
    for row in result.rows:
        # Never let a cell start a spreadsheet formula (names come from users and AD); some
        # spreadsheets skip a leading tab or carriage return first.
        writer.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else ("" if v is None else v) for v in row])
    return ("﻿" + buf.getvalue()).encode()  # BOM, so Excel reads UTF-8


def build_files(session: Session, spec: engine.ReportSpec, *, title: str, description: str, user: User, fmt: str, now: datetime) -> list[ReportFile]:
    result = engine.run(session, spec, user)
    ds = engine.dataset(spec.dataset)
    date_label = ds.dates.get(spec.date_field or next(iter(ds.dates)), ("",))[0]
    stem = f"{slug(title)}-{now:%Y-%m-%d}"
    files = []
    if fmt in ("pdf", "both"):
        data = pdf.render(result, title=title, subtitle=description, period=pdf.period_text(spec, date_label), generated_for=user.display_name, now=now)
        files.append(ReportFile(f"{stem}.pdf", "application/pdf", data))
    if fmt in ("csv", "both"):
        files.append(ReportFile(f"{stem}.csv", "text/csv", csv_bytes(result)))
    return files


# Email


def check_recipients(session: Session, recipients: list[str]) -> list[str]:
    """Clean, de-duplicated addresses; DeliveryError for any the service desk may not send reports to."""
    from app.directory.sync import find_by_address

    allowed_domains = {d.lower().lstrip("@") for d in settings.mail_allowed_domains}
    out: list[str] = []
    for raw in recipients:
        addr = raw.strip().lower()
        if not addr:
            continue
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
            raise DeliveryError(f"{raw!r} isn't an email address")
        user = find_by_address(session, [addr])
        if not ((user is not None and user.is_active) or addr.rsplit("@", 1)[1] in allowed_domains):
            raise DeliveryError(f"{addr} isn't a user here or in an accepted email domain (Settings > Email)")
        if addr not in out:
            out.append(addr)
    if not out:
        raise DeliveryError("Add at least one recipient")
    return out


def queue_email(session: Session, recipients: list[str], *, title: str, files: list[ReportFile], sender: str, message: str, period: str) -> int:
    """Queue the report to each recipient (sent by the worker through the service desk mailbox)."""
    if not settings.mailbox:
        raise DeliveryError("Email isn't set up (Settings > Email), so reports can't be emailed yet")
    stored = [{"filename": f.filename, "content_type": f.content_type, "storage_key": storage.save(f.data).key} for f in files]
    lines = [message.strip(), ""] if message.strip() else []
    lines += [f"{title}", period, "", f"Sent from {settings.product_name} by {sender}. The report is attached."]
    if settings.email_footer:
        lines += ["", "--", settings.email_footer]
    for addr in recipients:
        session.add(OutboundEmail(to_address=addr, subject=f"Report: {title}", body="\n".join(lines), attachments=stored))
    return len(recipients)


# What a schedule (or a saved report) runs


def report_for(session: Session, preset_key: str | None, saved_report_id: int | None) -> tuple[str, str, engine.ReportSpec]:
    if saved_report_id is not None:
        saved = session.get(SavedReport, saved_report_id)
        if saved is None:
            raise DeliveryError("The saved report was deleted")
        return saved.name, saved.description, engine.ReportSpec.model_validate(saved.spec)
    preset = presets.BY_KEY.get(preset_key or "")
    if preset is None:
        raise DeliveryError("Pick a report")
    return preset.title, preset.description, preset.spec


# Schedules


def _local(d: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(d, time(h, m)).astimezone()


def next_run(s: ReportSchedule | ScriptSchedule, after: datetime) -> datetime | None:
    """The first run time strictly after `after` (server local time), or None when done."""
    if s.frequency == "once":
        when = _local(s.start_date, s.time_of_day)
        return when if when > after else None
    day = max(s.start_date, after.astimezone().date())
    for _ in range(400):
        candidate = _local(day, s.time_of_day)
        if candidate > after and day >= s.start_date:
            if s.frequency == "daily":
                return candidate
            if s.frequency == "weekly" and day.weekday() in (s.weekdays or []):
                return candidate
            if s.frequency == "monthly":
                last = calendar.monthrange(day.year, day.month)[1]
                want = last if s.day_of_month == -1 else min(s.day_of_month or 1, last)
                if day.day == want:
                    return candidate
        day += timedelta(days=1)
    return None


def run_schedule(session: Session, s: ReportSchedule, now: datetime) -> int:
    """Send one schedule's report now. Returns emails queued; DeliveryError/ReportError if it can't."""
    owner = s.owner
    if owner is None or not owner.is_active:
        raise DeliveryError("Its owner is no longer an active user")
    if owner.role not in services.AGENT_ROLES:
        raise DeliveryError("Its owner is no longer a technician")
    title, description, spec = report_for(session, s.preset_key, s.saved_report_id)
    date_from, date_to = period_range(s.period, now.astimezone().date())
    spec = spec.model_copy(update={"date_from": date_from, "date_to": date_to})
    files = build_files(session, spec, title=title, description=description, user=owner, fmt=s.format, now=now)
    ds = engine.dataset(spec.dataset)
    period = pdf.period_text(spec, ds.dates.get(spec.date_field or next(iter(ds.dates)), ("",))[0])
    recipients = check_recipients(session, s.recipients)
    return queue_email(session, recipients, title=title, files=files, sender=f"{owner.display_name} (scheduled: {s.name})", message=s.message, period=period)


def run_due(session_factory, now: datetime | None = None) -> tuple[int, int]:
    """Worker job: send every schedule that's due. Returns (schedules run, failures)."""
    now = now or sla.utcnow()
    ran = failed = 0
    with session_factory() as session:
        due = list(session.scalars(select(ReportSchedule.id).where(ReportSchedule.is_active, ReportSchedule.next_run_at <= now)))
    for schedule_id in due:
        with session_factory() as session:
            s = session.scalar(select(ReportSchedule).where(ReportSchedule.id == schedule_id).with_for_update(skip_locked=True))
            if s is None or s.next_run_at is None or s.next_run_at > now:
                continue
            try:
                sent = run_schedule(session, s, now)
                s.last_error = None
                log.info("Scheduled report %r emailed to %s recipient(s)", s.name, sent)
                ran += 1
            except (DeliveryError, engine.ReportError) as exc:
                session.rollback()
                s = session.get(ReportSchedule, schedule_id)
                s.last_error = str(exc)
                log.warning("Scheduled report %r not sent: %s", s.name, exc)
                failed += 1
            s.last_run_at = now
            s.next_run_at = next_run(s, now)
            if s.next_run_at is None:
                s.is_active = False
            session.commit()
    return ran, failed
