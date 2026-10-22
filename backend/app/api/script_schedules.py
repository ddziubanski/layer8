"""Script schedules: a script run on a timetable, as its owner (app.scriptschedules)."""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app import scripts, scriptschedules, sla
from app.api.runs import _run_out
from app.auth import Agent, SessionDep
from app.models import Device, Script, ScriptRun, ScriptSchedule, User, UserRole
from app.reports import delivery
from app.schemas import ScriptScheduleIn, ScriptScheduleOut, SystemDeviceRef, UserRef

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/script-schedules", tags=["scripts"])
RECENT = 5


def _out(session, s: ScriptSchedule, user: User) -> ScriptScheduleOut:
    out = ScriptScheduleOut.model_validate(s)
    out.script_name, out.script_kind = s.script.name, s.script.kind.value
    out.owner = UserRef.model_validate(s.owner) if s.owner else None
    out.group_name = s.group_script.name if s.group_script else None
    if s.device_ids:
        out.devices = [SystemDeviceRef.model_validate(d) for d in session.scalars(select(Device).where(Device.id.in_(s.device_ids)).order_by(Device.name))]
    out.can_edit = user.role == UserRole.admin or s.owner_id == user.id
    recent = session.scalars(select(ScriptRun).where(ScriptRun.schedule_id == s.id).order_by(ScriptRun.id.desc()).limit(RECENT))
    out.recent_runs = [_run_out(r, user) for r in recent]
    return out


def _schedule(session, schedule_id: int, user: User) -> ScriptSchedule:
    s = session.get(ScriptSchedule, schedule_id)
    if s is None or not (user.role == UserRole.admin or s.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return s


def _apply(session, s: ScriptSchedule, body: ScriptScheduleIn) -> None:
    script = session.get(Script, body.script_id)
    if script is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such script")
    if scripts.is_builtin(script.name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Built-in scripts run from their own place in Layer 8 (e.g. a subnet's scan schedule), not on a script schedule")
    for field, value in body.model_dump().items():
        setattr(s, field, value)
    s.script = script
    s.group_script = session.get(Script, body.group_script_id) if body.group_script_id is not None else None
    s.device_ids = list(dict.fromkeys(body.device_ids))
    s.weekdays = sorted(set(body.weekdays))
    s.recipients = [r for r in body.recipients if r.strip()]
    try:
        scriptschedules.check(session, s)
    except scriptschedules.ScheduleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    s.next_run_at = delivery.next_run(s, sla.utcnow()) if s.is_active else None
    if s.is_active and s.next_run_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That schedule never runs: check the start date and time")


@router.get("", response_model=list[ScriptScheduleOut])
def list_schedules(user: Agent, session: SessionDep, script_id: int | None = None):
    """Your schedules (admins: everyone's), optionally for one script."""
    stmt = select(ScriptSchedule).order_by(ScriptSchedule.name)
    if user.role != UserRole.admin:
        stmt = stmt.where(ScriptSchedule.owner_id == user.id)
    if script_id is not None:
        stmt = stmt.where(ScriptSchedule.script_id == script_id)
    return [_out(session, s, user) for s in session.scalars(stmt)]


@router.post("", response_model=ScriptScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScriptScheduleIn, user: Agent, session: SessionDep):
    s = ScriptSchedule(owner_id=user.id)
    s.owner = user
    _apply(session, s, body)
    session.add(s)
    session.commit()
    log.info("%s scheduled script %r (%s)", user.display_name, s.script.name, s.frequency, extra={"source": "scripts"})
    return _out(session, s, user)


@router.put("/{schedule_id}", response_model=ScriptScheduleOut)
def update_schedule(schedule_id: int, body: ScriptScheduleIn, user: Agent, session: SessionDep):
    s = _schedule(session, schedule_id, user)
    _apply(session, s, body)
    session.commit()
    return _out(session, s, user)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: int, user: Agent, session: SessionDep):
    """Runs it already queued stay (and still run)."""
    session.delete(_schedule(session, schedule_id, user))
    session.commit()


@router.post("/{schedule_id}/run", response_model=ScriptScheduleOut)
def run_now(schedule_id: int, user: Agent, session: SessionDep):
    """Run it now, as the timetable would; the timetable doesn't change."""
    s = _schedule(session, schedule_id, user)
    now = sla.utcnow()
    try:
        s.last_result = scriptschedules.run_schedule(session, s, now)
    except scriptschedules.ScheduleError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    s.last_run_at, s.last_error = now, None
    session.commit()
    return _out(session, s, user)
