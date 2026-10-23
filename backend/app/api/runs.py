"""Runners (Settings > Runners), running action scripts, and the API runners poll.

Runners authenticate with their own bearer token (Authorization: Bearer hdr_...), never a
user session; they can only claim queued runs and report on the runs they hold.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import or_, select

from app import runs, scripts, sla
from app.api.tickets import staff_ticket, visible_queue_ids
from app.auth import Admin, Agent, SessionDep
from app.models import Device, Runner, RunStatus, Script, ScriptKind, ScriptRun, Ticket, User, UserRole
from app.schemas import (
    RunDetailOut,
    RunnerClaimIn,
    RunnerFinishIn,
    RunnerIn,
    RunnerJobOut,
    RunnerOut,
    RunnerOutputIn,
    RunnerTokenOut,
    RunOut,
    RunPreviewOut,
    RunRequestIn,
    RunTargetOut,
    UserRef,
)

router = APIRouter(tags=["runs"])
log = logging.getLogger(__name__)
ONLINE = timedelta(minutes=3)


def _err(exc: Exception, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, str(exc))


# Settings > Runners


def _runner(session, runner_id: int) -> Runner:
    runner = session.get(Runner, runner_id)
    if runner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    return runner


def _name_free(session, name: str, runner_id: int | None = None) -> None:
    other = session.scalar(select(Runner).where(Runner.name == name))
    if other is not None and other.id != runner_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a runner called {name!r}")


@router.get("/api/runners", response_model=list[RunnerOut])
def list_runners(_: Admin, session: SessionDep):
    return session.scalars(select(Runner).order_by(Runner.name)).all()


@router.post("/api/runners", response_model=RunnerTokenOut, status_code=status.HTTP_201_CREATED)
def create_runner(body: RunnerIn, admin: Admin, session: SessionDep):
    name = body.name.strip()
    _name_free(session, name)
    token, token_hash = runs.new_token()
    runner = Runner(name=name, description=body.description, languages=sorted(set(body.languages)), is_active=body.is_active, builtins_only=body.builtins_only, token_hash=token_hash)
    session.add(runner)
    session.commit()
    log.info("Runner %r added by %s", runner.name, admin.display_name, extra={"source": "settings"})
    return RunnerTokenOut(runner=RunnerOut.model_validate(runner), token=token)


@router.put("/api/runners/{runner_id}", response_model=RunnerOut)
def update_runner(runner_id: int, body: RunnerIn, admin: Admin, session: SessionDep):
    runner = _runner(session, runner_id)
    name = body.name.strip()
    _name_free(session, name, runner.id)
    runner.name, runner.description, runner.languages, runner.is_active = name, body.description, sorted(set(body.languages)), body.is_active
    if "builtins_only" in body.model_fields_set:
        runner.builtins_only = body.builtins_only
    session.commit()
    return runner


@router.post("/api/runners/{runner_id}/token", response_model=RunnerTokenOut)
def new_runner_token(runner_id: int, admin: Admin, session: SessionDep):
    """A new token; the old one stops working at once."""
    runner = _runner(session, runner_id)
    token, runner.token_hash = runs.new_token()
    session.commit()
    log.warning("Runner %r given a new token by %s", runner.name, admin.display_name, extra={"source": "settings"})
    return RunnerTokenOut(runner=RunnerOut.model_validate(runner), token=token)


@router.delete("/api/runners/{runner_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_runner(runner_id: int, admin: Admin, session: SessionDep):
    runner = _runner(session, runner_id)
    # SET NULL on the FK must never turn a pinned privileged job into general work.
    for run in session.scalars(select(ScriptRun).where(
        ScriptRun.only_runner_id == runner.id,
        ScriptRun.status.in_((RunStatus.queued, RunStatus.awaiting_approval)),
    ).with_for_update()):
        # Cancelled without taking over decided_by: whoever approved it stays on record.
        runs._revoke(session, run, "Selected runner removed; request a new run on an appropriate runner")
    session.delete(runner)
    session.commit()
    log.info("Runner %r removed by %s", runner.name, admin.display_name, extra={"source": "settings"})


# Running action scripts


def _run_out(run: ScriptRun, user: User, cls=RunOut, **extra):
    return cls(
        id=run.id, script_id=run.script_id, script_name=run.script.name, version=run.version.number, language=run.version.language or run.script.language,
        device_id=run.device_id, target=run.target, values=run.values, ticket_id=run.ticket_id, status=run.status,
        requested_by=UserRef.model_validate(run.requested_by) if run.requested_by else None,
        decided_by=UserRef.model_validate(run.decided_by) if run.decided_by else None,
        runner_name=run.runner.name if run.runner else None, created_at=run.created_at, not_before=run.not_before, claimed_at=run.claimed_at,
        finished_at=run.finished_at, exit_code=run.exit_code, error=run.error, schedule_id=run.schedule_id,
        can_decide=user.role == UserRole.admin and run.status == RunStatus.awaiting_approval and run.requested_by_id != user.id,
        can_cancel=run.status in (RunStatus.awaiting_approval, RunStatus.queued) and (user.role == UserRole.admin or run.requested_by_id == user.id),
        **extra,
    )


def _action(session, script_id: int, user: User) -> Script:
    script = session.get(Script, script_id)
    if script is None or (not script.is_active and user.role != UserRole.admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    if script.kind != ScriptKind.action:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only actions run on devices")
    if scripts.is_builtin(script.name):
        # They run from their own feature (IP addresses, a ticket's AD account card), which checks
        # what they may touch; here nothing would.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Built-in scripts run from their own place in Layer 8 (IP addresses, a ticket's AD account card), not from here")
    return script


def _targets(session, body: RunRequestIn, user: User) -> list[int]:
    """Device ids from the request: picked ones, plus a dynamic group's."""
    ids = list(body.device_ids)
    if body.group_script_id is not None:
        try:
            ids += runs.group_device_ids(session, body.group_script_id, user)
        except runs.RunError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return list(dict.fromkeys(ids))


@router.post("/api/scripts/{script_id}/preview", response_model=RunPreviewOut)
def preview_run(script_id: int, body: RunRequestIn, user: Agent, session: SessionDep):
    """What a run would do: its targets, its parameter values, whether it needs approval and a runner is about. Nothing runs."""
    script = _action(session, script_id, user)
    version = next(v for v in script.versions if v.number == script.current_version)
    try:
        values = scripts.values_for(version.parameters, body.values)
    except scripts.ScriptError as exc:
        raise _err(exc) from None
    targets = []
    for device_id in _targets(session, body, user):
        d = session.get(Device, device_id)
        if d is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No device {device_id}")
        targets.append(RunTargetOut(device_id=d.id, name=d.name, target=runs.target_name(d)))
    if body.on_runner:
        targets.append(RunTargetOut(device_id=None, name="On the runner itself", target=None))
    able = [r for r in session.scalars(select(Runner).where(Runner.is_active, Runner.builtins_only.is_(False))) if script.language in (r.languages or [])]
    online = any(r.last_seen_at and sla.utcnow() - r.last_seen_at < ONLINE for r in able)
    return RunPreviewOut(
        targets=targets, values={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in values.items()},
        needs_approval=script.high_risk, runners=[r.name for r in able], runner_online=online,
    )


@router.post("/api/scripts/{script_id}/runs", response_model=list[RunOut], status_code=status.HTTP_201_CREATED)
def request_runs(script_id: int, body: RunRequestIn, user: Agent, session: SessionDep):
    script = _action(session, script_id, user)
    ticket = staff_ticket(session, body.ticket_id, user) if body.ticket_id is not None else None
    try:
        made = runs.request(session, script, user, device_ids=_targets(session, body, user), on_runner=body.on_runner, values=body.values, ticket=ticket)
    except runs.RunError as exc:
        session.rollback()
        raise _err(exc) from None
    session.commit()
    log.info("%s ran %r on %s target(s)%s", user.display_name, script.name, len(made), f" from ticket #{ticket.id}" if ticket else "", extra={"source": "scripts"})
    return [_run_out(r, user) for r in made]


def _visible_runs(user: User):
    stmt = select(ScriptRun).join(Script, Script.id == ScriptRun.script_id)
    if user.role != UserRole.admin:
        stmt = stmt.where(or_(Script.agents_can_run, ScriptRun.requested_by_id == user.id))
        # A run from a ticket in a restricted queue is as private as the ticket.
        stmt = stmt.where(or_(ScriptRun.ticket_id.is_(None), ScriptRun.ticket_id.in_(select(Ticket.id).where(Ticket.queue_id.in_(visible_queue_ids(user))))))
    return stmt


@router.get("/api/runs", response_model=list[RunOut])
def list_runs(
    user: Agent, session: SessionDep, script_id: int | None = None, ticket_id: int | None = None, run_status: str | None = Query(None, alias="status"), limit: int = 100
):
    stmt = _visible_runs(user)
    if script_id is not None:
        stmt = stmt.where(ScriptRun.script_id == script_id)
    if ticket_id is not None:
        staff_ticket(session, ticket_id, user)
        stmt = stmt.where(ScriptRun.ticket_id == ticket_id)
    if run_status:
        stmt = stmt.where(ScriptRun.status == run_status)
    return [_run_out(r, user) for r in session.scalars(stmt.order_by(ScriptRun.id.desc()).limit(min(limit, 500)))]


def _visible_run(session, run_id: int, user: User) -> ScriptRun:
    run = session.scalar(_visible_runs(user).where(ScriptRun.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.get("/api/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: int, user: Agent, session: SessionDep):
    run = _visible_run(session, run_id, user)
    return _run_out(run, user, RunDetailOut, output=run.output, body=run.version.body)


def _decide(run_id: int, admin: User, session, approve: bool) -> RunOut:
    run = _visible_run(session, run_id, admin)
    try:
        runs.decide(session, run, admin, approve)
    except runs.RunError as exc:
        raise _err(exc) from None
    session.commit()
    log.info("%s %s run #%s of %r", admin.display_name, "approved" if approve else "rejected", run.id, run.script.name, extra={"source": "scripts"})
    return _run_out(run, admin)


@router.post("/api/runs/{run_id}/approve", response_model=RunOut)
def approve_run(run_id: int, admin: Admin, session: SessionDep):
    return _decide(run_id, admin, session, True)


@router.post("/api/runs/{run_id}/reject", response_model=RunOut)
def reject_run(run_id: int, admin: Admin, session: SessionDep):
    return _decide(run_id, admin, session, False)


@router.post("/api/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, user: Agent, session: SessionDep):
    run = _visible_run(session, run_id, user)
    try:
        runs.cancel(session, run, user)
    except runs.RunError as exc:
        raise _err(exc) from None
    session.commit()
    return _run_out(run, user)


# What runners call


def _calling_runner(session, authorization: str | None) -> Runner:
    token = (authorization or "").removeprefix("Bearer ").strip()
    runner = runs.runner_for_token(session, token) if token else None
    if runner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled runner token", headers={"WWW-Authenticate": "Bearer"})
    return runner


@router.post("/api/runner/claim", response_model=RunnerJobOut, responses={204: {"description": "Nothing to run"}})
def runner_claim(body: RunnerClaimIn, session: SessionDep, authorization: str | None = Header(default=None)):
    runner = _calling_runner(session, authorization)
    run, secret = runs.claim(session, runner, body.host, body.version)
    session.commit()
    if run is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    log.info("Runner %r started run #%s (%r on %s)", runner.name, run.id, run.script.name, run.target or "itself", extra={"source": "scripts"})
    return RunnerJobOut(
        run_id=run.id, language=run.version.language or run.script.language, body=run.version.body, values=run.values, target=run.target,
        target_os=run.device.os if run.device else None, time_limit_seconds=runs.TIME_LIMIT_SECONDS, secret_values=secret,
    )


@router.post("/api/runner/runs/{run_id}/output", status_code=status.HTTP_204_NO_CONTENT)
def runner_output(run_id: int, body: RunnerOutputIn, session: SessionDep, authorization: str | None = Header(default=None)):
    runner = _calling_runner(session, authorization)
    try:
        runs.add_output(session, runner, run_id, body.text)
    except runs.RunError as exc:
        raise _err(exc, status.HTTP_409_CONFLICT) from None
    session.commit()


@router.post("/api/runner/runs/{run_id}/finish", status_code=status.HTTP_204_NO_CONTENT)
def runner_finish(run_id: int, body: RunnerFinishIn, session: SessionDep, authorization: str | None = Header(default=None)):
    runner = _calling_runner(session, authorization)
    try:
        run = runs.finish(session, runner, run_id, body.exit_code, body.error)
    except runs.RunError as exc:
        raise _err(exc, status.HTTP_409_CONFLICT) from None
    session.commit()
    log.info("Run #%s of %r on %s: %s", run.id, run.script.name, run.target or "the runner", run.status, extra={"source": "scripts"})
