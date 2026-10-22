"""Reports for agents and admins: ready-made reports, the report builder's options, and running
a report as JSON or CSV. Agents only see tickets from queues they may see."""

from datetime import date

import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import or_, select

from app import sla
from app.auth import Agent, SessionDep
from app.models import ReportSchedule, SavedReport, User, UserRole
from app.reports import delivery, engine, pdf, presets
from app.schemas import (
    ReportDatasetOut,
    ReportDatasetRef,
    ReportEmailIn,
    ReportEmailOut,
    ReportExportIn,
    ReportPresetOut,
    ReportScheduleIn,
    ReportScheduleOut,
    SavedReportIn,
    SavedReportOut,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])
log = logging.getLogger(__name__)


@router.get("/presets", response_model=list[ReportPresetOut])
def list_presets(user: Agent):
    allowed = {d["name"] for d in engine.available(user)}
    return [
        ReportPresetOut(key=p.key, title=p.title, description=p.description, group=p.group, spec=p.spec)
        for p in presets.PRESETS
        if p.spec.dataset in allowed
    ]


@router.get("/datasets", response_model=list[ReportDatasetRef])
def list_datasets(user: Agent):
    """What this person can report on (email is for admins)."""
    return engine.available(user)


@router.get("/datasets/{name}", response_model=ReportDatasetOut)
def describe_dataset(name: str, _: Agent):
    try:
        return engine.describe(name)
    except engine.ReportError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


def _run(spec: engine.ReportSpec, user, session) -> engine.ReportResult:
    try:
        return engine.run(session, spec, user)
    except engine.ReportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/run", response_model=engine.ReportResult)
def run_report(spec: engine.ReportSpec, user: Agent, session: SessionDep):
    return _run(spec, user, session)


@router.post("/run.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def run_report_csv(spec: engine.ReportSpec, user: Agent, session: SessionDep):
    result = _run(spec, user, session)
    name = f"report-{spec.dataset}-{date.today().isoformat()}.csv"
    return Response(
        content=delivery.csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/run.pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def run_report_pdf(body: ReportExportIn, user: Agent, session: SessionDep):
    try:
        (file,) = delivery.build_files(session, body.spec, title=body.title, description=body.description, user=user, fmt="pdf", now=sla.utcnow())
    except engine.ReportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return Response(content=file.data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{file.filename}"'})


@router.post("/email", response_model=ReportEmailOut)
def email_report(body: ReportEmailIn, user: Agent, session: SessionDep):
    """Email the report now, as PDF and/or CSV, from the service desk mailbox."""
    now = sla.utcnow()
    try:
        recipients = delivery.check_recipients(session, body.recipients)
        files = delivery.build_files(session, body.spec, title=body.title, description=body.description, user=user, fmt=body.format, now=now)
        ds = engine.dataset(body.spec.dataset)
        period = pdf.period_text(body.spec, ds.dates.get(body.spec.date_field or next(iter(ds.dates)), ("",))[0])
        queued = delivery.queue_email(session, recipients, title=body.title, files=files, sender=user.display_name, message=body.message, period=period)
    except (delivery.DeliveryError, engine.ReportError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    log.info("%s emailed report %r to %s", user.display_name, body.title, ", ".join(recipients))
    return ReportEmailOut(queued=queued)


# Saved reports


def _out(report: SavedReport, user: User) -> SavedReportOut:
    out = SavedReportOut.model_validate(report)
    out.can_edit = user.role == UserRole.admin or report.owner_id == user.id
    return out


def _visible_report(session, report_id: int, user: User) -> SavedReport:
    report = session.get(SavedReport, report_id)
    if report is None or not (report.shared or report.owner_id == user.id or user.role == UserRole.admin) or (report.spec.get("dataset") == "sql" and user.role != UserRole.admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return report


def _editable_report(session, report_id: int, user: User) -> SavedReport:
    report = _visible_report(session, report_id, user)
    if not (user.role == UserRole.admin or report.owner_id == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only its owner or an admin can change this report")
    return report


def _check(spec: engine.ReportSpec, user: User) -> None:
    """Refuse a spec that couldn't run (unknown fields), before it's saved."""
    if spec.dataset == "sql" and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SQL reports are for admins")
    try:
        engine.validate(spec)
    except engine.ReportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/saved", response_model=list[SavedReportOut])
def list_saved(user: Agent, session: SessionDep):
    """Your own reports and everyone's shared ones (admins see all)."""
    stmt = select(SavedReport).order_by(SavedReport.name)
    if user.role != UserRole.admin:
        # SQL reports run on the rules login, which sees every queue: admins only.
        stmt = stmt.where(or_(SavedReport.shared, SavedReport.owner_id == user.id), SavedReport.spec["dataset"].astext.is_distinct_from("sql"))
    return [_out(r, user) for r in session.scalars(stmt)]


@router.post("/saved", response_model=SavedReportOut, status_code=status.HTTP_201_CREATED)
def save_report(body: SavedReportIn, user: Agent, session: SessionDep):
    _check(body.spec, user)
    report = SavedReport(name=body.name.strip(), description=body.description, spec=body.spec.model_dump(mode="json"), shared=body.shared, owner_id=user.id)
    session.add(report)
    session.commit()
    return _out(report, user)


@router.put("/saved/{report_id}", response_model=SavedReportOut)
def update_saved(report_id: int, body: SavedReportIn, user: Agent, session: SessionDep):
    report = _editable_report(session, report_id, user)
    _check(body.spec, user)
    report.name, report.description, report.shared = body.name.strip(), body.description, body.shared
    report.spec = body.spec.model_dump(mode="json")
    session.commit()
    return _out(report, user)


@router.delete("/saved/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved(report_id: int, user: Agent, session: SessionDep):
    session.delete(_editable_report(session, report_id, user))
    session.commit()


# Schedules


def _schedule_out(session, s: ReportSchedule, user: User) -> ReportScheduleOut:
    out = ReportScheduleOut.model_validate(s)
    try:
        out.report_title = delivery.report_for(session, s.preset_key, s.saved_report_id)[0]
    except delivery.DeliveryError:
        out.report_title = "(report deleted)"
    out.can_edit = user.role == UserRole.admin or s.owner_id == user.id
    return out


def _schedule(session, schedule_id: int, user: User) -> ReportSchedule:
    s = session.get(ReportSchedule, schedule_id)
    if s is None or not (user.role == UserRole.admin or s.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return s


def _apply(session, s: ReportSchedule, body: ReportScheduleIn, user: User) -> None:
    if (body.preset_key is None) == (body.saved_report_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick one report: a ready-made one or a saved one")
    if body.saved_report_id is not None:
        _visible_report(session, body.saved_report_id, user)
    if body.period not in delivery.PERIODS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown period {body.period!r}")
    if body.frequency == "weekly" and not body.weekdays:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick at least one day of the week")
    if body.frequency == "monthly" and body.day_of_month in (None, 0):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick the day of the month")
    try:
        delivery.report_for(session, body.preset_key, body.saved_report_id)
        recipients = delivery.check_recipients(session, body.recipients)
    except delivery.DeliveryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    for field, value in body.model_dump(exclude={"recipients"}).items():
        setattr(s, field, value)
    s.weekdays = sorted(set(body.weekdays))
    s.recipients = recipients
    s.next_run_at = delivery.next_run(s, sla.utcnow()) if s.is_active else None
    if s.is_active and s.next_run_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That schedule never runs: check the start date and time")


@router.get("/schedules", response_model=list[ReportScheduleOut])
def list_schedules(user: Agent, session: SessionDep):
    """Your schedules (admins: everyone's)."""
    stmt = select(ReportSchedule).order_by(ReportSchedule.name)
    if user.role != UserRole.admin:
        stmt = stmt.where(ReportSchedule.owner_id == user.id)
    return [_schedule_out(session, s, user) for s in session.scalars(stmt)]


@router.post("/schedules", response_model=ReportScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ReportScheduleIn, user: Agent, session: SessionDep):
    s = ReportSchedule(owner_id=user.id)
    s.owner = user
    _apply(session, s, body, user)
    session.add(s)
    session.commit()
    log.info("%s scheduled report %r (%s)", user.display_name, s.name, s.frequency)
    return _schedule_out(session, s, user)


@router.put("/schedules/{schedule_id}", response_model=ReportScheduleOut)
def update_schedule(schedule_id: int, body: ReportScheduleIn, user: Agent, session: SessionDep):
    s = _schedule(session, schedule_id, user)
    _apply(session, s, body, user)
    session.commit()
    return _schedule_out(session, s, user)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: int, user: Agent, session: SessionDep):
    session.delete(_schedule(session, schedule_id, user))
    session.commit()


@router.post("/schedules/{schedule_id}/send", response_model=ReportEmailOut)
def send_schedule_now(schedule_id: int, user: Agent, session: SessionDep):
    """Send it now, as the schedule would; its timetable doesn't change."""
    s = _schedule(session, schedule_id, user)
    try:
        queued = delivery.run_schedule(session, s, sla.utcnow())
    except (delivery.DeliveryError, engine.ReportError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    return ReportEmailOut(queued=queued)


@router.get("/periods")
def report_periods(_: Agent) -> dict[str, str]:
    """Relative periods a schedule can use."""
    return delivery.PERIODS
