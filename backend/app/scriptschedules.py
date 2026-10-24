# SPDX-License-Identifier: AGPL-3.0-or-later
"""Script schedules (Scripts > Schedules): a script run on a timetable, as its owner.

An action is queued on its devices, its dynamic group's devices (worked out at each run, so the
group's membership on the day counts) or the runner itself, through app.runs like a person's run:
a high-risk script still waits for an admin's approval each time (not the owner's own). A query
runs at once and its result is emailed as CSV. The timetable works as a report schedule's
(app.reports.delivery.next_run).
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import runs, scripts, sla
from app.models import ScriptKind, ScriptSchedule, UserRole
from app.reports import delivery, engine, sqlreport

log = logging.getLogger(__name__)


class ScheduleError(ValueError):
    """The message is safe to show."""


def check(session: Session, s: ScriptSchedule) -> None:
    """Everything a run needs, as far as it can be known before it runs."""
    script = s.script
    if script is None or not script.is_active:
        raise ScheduleError("That script isn't in use")
    owner = s.owner
    if owner is None or not owner.is_active:
        raise ScheduleError("Its owner is no longer an active user")
    if not (owner.role == UserRole.admin or script.agents_can_run):
        raise ScheduleError(f"{owner.display_name} can't run this script")
    version = next(v for v in script.versions if v.number == script.current_version)
    try:
        scripts.values_for(version.parameters, s.values)
    except scripts.ScriptError as exc:
        raise ScheduleError(str(exc)) from None
    if s.frequency == "weekly" and not s.weekdays:
        raise ScheduleError("Pick at least one day of the week")
    if s.frequency == "monthly" and s.day_of_month in (None, 0):
        raise ScheduleError("Pick the day of the month")
    if script.kind == ScriptKind.action:
        if s.on_runner == bool(s.device_ids or s.group_script_id):
            raise ScheduleError("Pick the devices (or a device group) to run it on, or run it on the runner itself")
        if len(s.device_ids) > runs.MAX_TARGETS:
            raise ScheduleError(f"At most {runs.MAX_TARGETS} devices")
    else:
        if s.period and s.period not in delivery.PERIODS:
            raise ScheduleError(f"Unknown period {s.period!r}")
        try:
            s.recipients = delivery.check_recipients(session, s.recipients)
        except delivery.DeliveryError as exc:
            raise ScheduleError(str(exc)) from None


def run_schedule(session: Session, s: ScriptSchedule, now: datetime) -> str:
    """Run it now, as the timetable would. Returns what it did; ScheduleError if it can't. Doesn't commit."""
    check(session, s)
    script, owner = s.script, s.owner
    if script.kind == ScriptKind.action:
        try:
            ids = list(s.device_ids)
            if s.group_script_id is not None:
                ids += runs.group_device_ids(session, s.group_script_id, owner)
            ids = list(dict.fromkeys(ids))
            if not ids and not s.on_runner:
                return "Nothing to do: the group has no devices today"
            made = runs.request(session, script, owner, device_ids=ids, on_runner=s.on_runner, values=s.values, ticket=None, schedule_id=s.id)
        except runs.RunError as exc:
            raise ScheduleError(str(exc)) from None
        where = "on the runner" if s.on_runner else f"on {len(made)} device(s)"
        return f"Queued {where}" + (", waiting for an admin's approval" if script.high_risk else "")
    version = next(v for v in script.versions if v.number == script.current_version)
    date_from, date_to = delivery.period_range(s.period, now.astimezone().date()) if s.period else (None, None)
    try:
        result = sqlreport.run(version.body, date_from, date_to, engine.MAX_ROWS, scripts.values_for(version.parameters, s.values))
        stem = delivery.slug(s.name)
        files = [delivery.ReportFile(f"{stem}-{now.astimezone():%Y-%m-%d}.csv", "text/csv", delivery.csv_bytes(result))]
        period = delivery.PERIODS.get(s.period, "") if s.period else ""
        sent = delivery.queue_email(session, s.recipients, title=s.name, files=files, sender=f"{owner.display_name} (scheduled: {s.name})", message=s.message, period=period)
    except (engine.ReportError, scripts.ScriptError, delivery.DeliveryError) as exc:
        raise ScheduleError(str(exc)) from None
    more = f" (the first {engine.MAX_ROWS})" if result.truncated else ""
    return f"Emailed {len(result.rows)} row(s){more} to {sent} recipient(s)"


def run_due(session_factory, now: datetime | None = None) -> tuple[int, int]:
    """Worker job: run every schedule that's due. Returns (schedules run, failures)."""
    now = now or sla.utcnow()
    ran = failed = 0
    with session_factory() as session:
        due = list(session.scalars(select(ScriptSchedule.id).where(ScriptSchedule.is_active, ScriptSchedule.next_run_at <= now)))
    for schedule_id in due:
        with session_factory() as session:
            s = session.scalar(select(ScriptSchedule).where(ScriptSchedule.id == schedule_id).with_for_update(skip_locked=True))
            if s is None or s.next_run_at is None or s.next_run_at > now:
                continue
            try:
                s.last_result, s.last_error = run_schedule(session, s, now), None
                log.info("Script schedule %r: %s", s.name, s.last_result, extra={"source": "scripts"})
                ran += 1
            except ScheduleError as exc:
                session.rollback()
                s = session.get(ScriptSchedule, schedule_id)
                s.last_result, s.last_error = "", str(exc)
                log.warning("Script schedule %r didn't run: %s", s.name, exc, extra={"source": "scripts"})
                failed += 1
            s.last_run_at = now
            s.next_run_at = delivery.next_run(s, now)
            if s.next_run_at is None:
                s.is_active = False
            session.commit()
    return ran, failed
