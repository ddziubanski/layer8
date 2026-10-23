"""Running action scripts: requesting runs (one per target), approval, and the runner's side
(claim, output, finish). Runs started from a ticket are logged on it.

A run's lifecycle: awaiting_approval (high-risk scripts) -> queued -> running -> succeeded or
failed; or rejected / cancelled before it starts. A runner only claims queued runs in the
languages it takes; a run still "running" long after its time limit is failed by housekeeping.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import runseal, scripts, services, sla
from app.models import (
    Device,
    EventType,
    OutboundEmail,
    Runner,
    RunStatus,
    Script,
    ScriptKind,
    ScriptRun,
    ScriptVersion,
    Ticket,
    TicketSource,
    User,
    UserRole,
)

# Sent to the runner with each run; it stops the script after this long.
TIME_LIMIT_SECONDS = 600
# A run still "running" this long after its time limit lost its runner.
STALE_GRACE = timedelta(minutes=5)
MAX_OUTPUT = 1_000_000
MAX_TARGETS = 200
OPEN = (RunStatus.awaiting_approval, RunStatus.queued, RunStatus.running)


class RunError(ValueError):
    """The message is safe to show."""


def new_token() -> tuple[str, str]:
    """(token to show once, hash to keep)."""
    token = "hdr_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def runner_for_token(session: Session, token: str) -> Runner | None:
    runner = session.scalar(select(Runner).where(Runner.token_hash == hash_token(token)))
    return runner if runner is not None and runner.is_active else None


def target_name(device: Device | None) -> str | None:
    return (device.dns_hostname or device.name) if device else None


def group_device_ids(session: Session, group_script_id: int, user: User) -> list[int]:
    """A dynamic group's devices: a query the user may run that returns a device_id column."""
    from app.reports import engine, sqlreport

    group = session.get(Script, group_script_id)
    if group is None or group.kind != ScriptKind.query or not group.is_active or not (user.role == UserRole.admin or group.agents_can_run):
        raise RunError("That group isn't a query you can run")
    v = next(x for x in group.versions if x.number == group.current_version)
    try:
        result = sqlreport.run(v.body, None, None, MAX_TARGETS, scripts.values_for(v.parameters, {}))
    except (scripts.ScriptError, engine.ReportError) as exc:
        raise RunError(str(exc)) from None
    col = next((i for i, c in enumerate(result.columns) if c.label == "device_id"), None)
    if col is None:
        raise RunError(f"{group.name!r} doesn't return a device_id column, so it isn't a device group")
    if result.truncated:
        raise RunError(f"{group.name!r} returns more than {MAX_TARGETS} devices")
    return [int(r[col]) for r in result.rows if r[col] is not None]


def _ticket_note(session: Session, ticket: Ticket, author: User, body: str) -> None:
    services.add_comment(session, ticket, author, body=body, is_internal=True, source=TicketSource.agent, as_staff=True)


def request(
    session: Session,
    script: Script,
    user: User,
    *,
    device_ids: list[int],
    on_runner: bool,
    values: dict,
    ticket: Ticket | None,
    sealed: dict | None = None,
    only_runner_id: int | None = None,
    not_before: datetime | None = None,
    on_success: dict | None = None,
    schedule_id: int | None = None,
) -> list[ScriptRun]:
    """Queue the script on each device (or once on the runner itself). Doesn't commit.
    `sealed`: values only the runner gets (see app.runseal); never stored in the clear or shown.
    `not_before`: not claimed before then. `on_success`: an email ({to, subject, body}) sent if it succeeds."""
    session.flush()
    session.refresh(script, with_for_update=True)
    session.expire(script, ["versions"])
    if script.kind != ScriptKind.action:
        raise RunError("Queries run straight away; only actions go to a runner")
    if not script.is_active:
        raise RunError("This script isn't in use")
    if not (user.role == UserRole.admin or script.agents_can_run):
        raise RunError("You can't run this script")
    version = next(v for v in script.versions if v.number == script.current_version)
    try:
        typed = scripts.values_for(version.parameters, values)
    except scripts.ScriptError as exc:
        raise RunError(str(exc)) from None
    # JSON-safe: dates as text.
    typed = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in typed.items()}
    devices = []
    for device_id in dict.fromkeys(device_ids):
        device = session.get(Device, device_id)
        if device is None:
            raise RunError(f"No device {device_id}")
        devices.append(device)
    if on_runner == bool(devices):
        raise RunError("Pick the devices to run it on, or run it on the runner itself")
    if len(devices) > MAX_TARGETS:
        raise RunError(f"At most {MAX_TARGETS} devices at a time")
    status = RunStatus.awaiting_approval if script.high_risk else RunStatus.queued
    made = []
    for device in devices or [None]:
        run = ScriptRun(
            script_id=script.id, version_id=version.id, device_id=device.id if device else None, target=target_name(device),
            values=typed, ticket_id=ticket.id if ticket else None, requested_by_id=user.id, status=status,
            sealed=runseal.seal(sealed) if sealed else None, only_runner_id=only_runner_id, not_before=not_before, on_success=on_success,
            schedule_id=schedule_id,
        )
        session.add(run)
        made.append(run)
    session.flush()
    if ticket is not None:
        where = ", ".join(r.target for r in made if r.target) or "the runner"
        services._event(session, ticket, EventType.script_run, user, script=script.name, version=version.number, runs=[r.id for r in made], targets=where)
        wait = " It needs an admin's approval first." if script.high_risk else ""
        if not_before is not None:
            wait += f" It waits until {not_before.astimezone():%A %d %B %Y, %H:%M}."
        _ticket_note(session, ticket, user, f"Ran script \"{script.name}\" (version {version.number}) on {where}.{wait}")
    return made


def decide(session: Session, run: ScriptRun, admin: User, approve: bool) -> None:
    session.refresh(run, with_for_update=True)
    if admin.role != UserRole.admin or not admin.is_active:
        raise RunError("Only active admins may decide runs")
    if run.status != RunStatus.awaiting_approval:
        raise RunError("This run isn't waiting for approval")
    if run.requested_by_id == admin.id:
        raise RunError("Someone else has to approve your own run")
    if approve and run.version.author_id == admin.id:
        raise RunError("Someone else has to approve code you authored")
    if approve and _check(session, run, approving=True) == "cancel":
        raise RunError("Execution authorization changed; request a new run")
    run.status = RunStatus.queued if approve else RunStatus.rejected
    run.decided_by_id, run.decided_at = admin.id, sla.utcnow()
    if not approve:
        run.sealed = None
        run.finished_at = run.decided_at
        _log_finished(session, run)
        from app import ipamscan

        ipamscan.on_finished(session, run)


def cancel(session: Session, run: ScriptRun, user: User) -> None:
    if run.status not in (RunStatus.awaiting_approval, RunStatus.queued):
        raise RunError("Only a run that hasn't started can be cancelled")
    if not (user.role == UserRole.admin or run.requested_by_id == user.id):
        raise RunError("Only whoever asked for it, or an admin, can cancel it")
    run.status, run.sealed = RunStatus.cancelled, None
    run.decided_by_id, run.finished_at = user.id, sla.utcnow()
    from app import ipamscan

    ipamscan.on_finished(session, run)


# The runner's side


def cancel_pending(session: Session, script: Script, reason: str) -> None:
    for run in session.scalars(select(ScriptRun).where(
        ScriptRun.script_id == script.id,
        ScriptRun.status.in_((RunStatus.queued, RunStatus.awaiting_approval)),
    ).with_for_update()):
        _revoke(session, run, reason)


def _requester_authorized(session: Session, run: ScriptRun) -> bool:
    user = run.requested_by
    if user is None:
        return False
    if user.is_active:
        return user.role == UserRole.admin or (user.role == UserRole.agent and run.script.agents_can_run)
    # Scheduled discovery deliberately uses a non-login service identity. Preserve that
    # narrow worker path, not arbitrary inactive users or generic scripts using its name.
    if user.email == services.AUTOMATION_EMAIL and user.role == UserRole.agent and run.script.agents_can_run:
        from app import ipamscan
        from app.models import IpScan
        names = {definition[0] for definition in ipamscan.SCRIPTS.values()}
        return run.script.name in names and session.scalar(select(IpScan.id).where(IpScan.run_id == run.id, IpScan.scheduled)) is not None
    return False


def _check(session: Session, run: ScriptRun, *, approving: bool = False) -> str:
    """"ok" to run, "approval" when an active admin (not the requester or the code's author) must
    approve it first, "cancel" when it may no longer run at all. `approving`: as if approved now."""
    script = run.script
    if not script.is_active or run.version.number != script.current_version:
        return "cancel"
    requester_ok = _requester_authorized(session, run)
    # A built-in job booked for a date (a leaver's disable, a start-date enable) is never dropped
    # because its requester left or lost run rights since: an admin decides instead.
    if not requester_ok and not (scripts.is_builtin(script.name) and run.not_before is not None):
        return "cancel"
    if requester_ok and not script.high_risk:
        return "ok"
    if approving:
        return "ok"
    reviewer = run.decided_by
    if (reviewer is not None and reviewer.is_active and reviewer.role == UserRole.admin
            and reviewer.id not in (run.requested_by_id, run.version.author_id)):
        return "ok"
    return "approval"


def _revoke(session: Session, run: ScriptRun, reason: str) -> None:
    run.status, run.sealed, run.finished_at, run.error = RunStatus.cancelled, None, sla.utcnow(), reason
    _log_finished(session, run)
    from app import ipamscan
    ipamscan.on_finished(session, run)


def _hold_for_approval(session: Session, run: ScriptRun) -> None:
    run.status, run.decided_by_id, run.decided_at = RunStatus.awaiting_approval, None, None
    ticket = session.get(Ticket, run.ticket_id) if run.ticket_id else None
    if ticket is not None:
        _ticket_note(session, ticket, run.requested_by or services.automation_user(session),
                     f"Script \"{run.script.name}\" run #{run.id} now needs an admin's approval before it runs.")


def recheck_pending(session: Session, script: Script) -> None:
    """After a policy change (no new code): runs that may no longer go ahead are cancelled, and
    runs that now need an admin wait for one instead of being dropped."""
    for run in session.scalars(select(ScriptRun).where(
        ScriptRun.script_id == script.id,
        ScriptRun.status.in_((RunStatus.queued, RunStatus.awaiting_approval)),
    ).with_for_update()):
        verdict = _check(session, run)
        if verdict == "cancel":
            _revoke(session, run, "Script execution policy changed; request a new run")
        elif verdict == "approval" and run.status == RunStatus.queued:
            _hold_for_approval(session, run)


def claim(session: Session, runner: Runner, host: str, version: str) -> tuple[ScriptRun | None, dict]:
    """Mark the runner seen and give it the oldest queued run in its languages, if any, with its
    sealed values (which are cleared from the run here: the runner gets them once)."""
    runner.last_seen_at, runner.last_host, runner.last_version = sla.utcnow(), host[:255], version[:50]
    stmt = (
        select(ScriptRun)
        .join(Script, Script.id == ScriptRun.script_id)
        .join(ScriptVersion, ScriptVersion.id == ScriptRun.version_id)
        .where(
            ScriptRun.status == RunStatus.queued,
            or_(ScriptVersion.language.in_(runner.languages or []),
                (ScriptVersion.language.is_(None) & Script.language.in_(runner.languages or []))),
            or_(ScriptRun.only_runner_id.is_(None), ScriptRun.only_runner_id == runner.id),
            or_(ScriptRun.not_before.is_(None), ScriptRun.not_before <= sla.utcnow()),
        )
        .order_by(ScriptRun.id)
        .limit(1)
        .with_for_update(of=ScriptRun, skip_locked=True)
    )
    if runner.builtins_only:
        stmt = stmt.where(
            ScriptRun.only_runner_id == runner.id,
            func.lower(func.trim(Script.name)).startswith(scripts.BUILTIN_PREFIX.lower()),
        )
    while True:
        run = session.scalar(stmt)
        if run is None:
            return None, {}
        verdict = _check(session, run)
        if verdict == "ok":
            break
        if verdict == "approval":
            _hold_for_approval(session, run)
        else:
            _revoke(session, run, "Execution authorization changed; request a new run")
        session.flush()
    run.status, run.runner_id, run.claimed_at = RunStatus.running, runner.id, sla.utcnow()
    secret = runseal.unseal(run.sealed)
    run.sealed = None
    return run, secret


def _own_run(session: Session, runner: Runner, run_id: int) -> ScriptRun:
    run = session.get(ScriptRun, run_id)
    if run is None or run.runner_id != runner.id or run.status != RunStatus.running:
        raise RunError("Not a run this runner is running")
    return run


def add_output(session: Session, runner: Runner, run_id: int, text: str) -> None:
    run = _own_run(session, runner, run_id)
    runner.last_seen_at = sla.utcnow()
    room = MAX_OUTPUT - len(run.output)
    if room <= 0:
        return
    run.output += text[:room] + ("\n[output cut off]\n" if len(text) > room else "")


def finish(session: Session, runner: Runner, run_id: int, exit_code: int | None, error: str | None) -> ScriptRun:
    run = _own_run(session, runner, run_id)
    run.finished_at = runner.last_seen_at = sla.utcnow()
    run.exit_code, run.error = exit_code, (error or None) and error[:5000]
    run.status = RunStatus.succeeded if exit_code == 0 and not error else RunStatus.failed
    _log_finished(session, run)
    if run.status == RunStatus.succeeded and run.on_success and run.on_success.get("to"):
        email = run.on_success
        session.add(OutboundEmail(to_address=email["to"], subject=email.get("subject", "")[:300], body=email.get("body", ""), ticket_id=run.ticket_id))
    from app import ipamscan

    ipamscan.on_finished(session, run)
    return run


def _log_finished(session: Session, run: ScriptRun) -> None:
    """On the ticket it came from: how it went, with the end of its output."""
    if run.ticket_id is None:
        return
    ticket = session.get(Ticket, run.ticket_id)
    author = run.requested_by or services.automation_user(session)
    if ticket is None:
        return
    where = run.target or "the runner"
    if run.status == RunStatus.rejected:
        who = run.decided_by.display_name if run.decided_by else "an admin"
        _ticket_note(session, ticket, author, f"Script \"{run.script.name}\" on {where} was not approved ({who}).")
        return
    if run.status == RunStatus.cancelled:
        _ticket_note(session, ticket, author, f"Script \"{run.script.name}\" on {where} was cancelled. {run.error or ''} Run #{run.id}.")
        return
    head = f"Script \"{run.script.name}\" on {where} {'finished' if run.status == RunStatus.succeeded else 'failed'}"
    head += f" (exit code {run.exit_code})" if run.exit_code is not None else ""
    if run.error:
        head += f": {run.error}"
    tail = run.output[-4000:].strip()
    cut = "…\n" if len(run.output) > 4000 else ""
    _ticket_note(session, ticket, author, f"{head}. Run #{run.id}." + (f"\n\n{cut}{tail}" if tail else ""))


def fail_stale(session: Session, now: datetime | None = None) -> int:
    """Runs whose runner went quiet past their time limit: failed. Returns how many."""
    now = now or sla.utcnow()
    cutoff = now - timedelta(seconds=TIME_LIMIT_SECONDS) - STALE_GRACE
    stale = list(session.scalars(select(ScriptRun).where(ScriptRun.status == RunStatus.running, ScriptRun.claimed_at < cutoff)))
    for run in stale:
        run.status, run.finished_at = RunStatus.failed, now
        run.error = "The runner stopped answering before the run finished"
        _log_finished(session, run)
        from app import ipamscan

        ipamscan.on_finished(session, run)
    return len(stale)
