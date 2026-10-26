# SPDX-License-Identifier: AGPL-3.0-or-later
"""The script library: queries and actions, with versions. Admins write scripts; who may run each is set on it."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import object_session

from app import permissions, runs, scripts, sla
from app.auth import Agent, SessionDep
from app.models import Script, ScriptKind, ScriptLanguage, ScriptVersion, User, UserRole
from app.reports import delivery, engine, sqlreport
from app.schemas import ScriptIn, ScriptOut, ScriptParam, ScriptQueryRunIn, ScriptVersionDetailOut, ScriptVersionOut, UserRef

router = APIRouter(prefix="/api/scripts", tags=["scripts"])
ScriptsManager = permissions.require("scripts_manage")
log = logging.getLogger(__name__)


def _manages(session, user: User) -> bool:
    return permissions.allowed(session, user, "scripts_manage")


def _can_run(script: Script, user: User) -> bool:
    return script.is_active and (user.role == UserRole.admin or script.agents_can_run)


def _current(script: Script) -> ScriptVersion:
    return next(v for v in script.versions if v.number == script.current_version)


def _review(version: ScriptVersion, admin: User) -> None:
    # Record the first review only; later policy changes do not rewrite provenance.
    if admin.role == UserRole.admin and version.reviewed_at is None:
        version.reviewed_by_id, version.reviewed_at = admin.id, sla.utcnow()


def _out(script: Script, user: User) -> ScriptOut:
    v = _current(script)
    return ScriptOut(
        id=script.id,
        name=script.name,
        description=script.description,
        kind=script.kind,
        language=script.language,
        high_risk=script.high_risk,
        agents_can_run=script.agents_can_run,
        is_active=script.is_active,
        current_version=script.current_version,
        body=v.body,
        parameters=[ScriptParam(**p) for p in v.parameters],
        updated_at=script.updated_at,
        updated_by=UserRef.model_validate(v.author) if v.author else None,
        can_run=_can_run(script, user),
        can_edit=_manages(object_session(script), user),
    )


def _script(session, script_id: int, user: User, *, for_update: bool = False) -> Script:
    stmt = select(Script).where(Script.id == script_id)
    script = session.scalar(stmt.with_for_update() if for_update else stmt)
    if script is None or (not script.is_active and not _manages(session, user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    return script


def _checked(body: ScriptIn) -> list[dict]:
    try:
        return scripts.check_definition(body.kind, body.language, [p.model_dump() for p in body.parameters])
    except scripts.ScriptError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


def _not_builtin(script: Script, user: User) -> None:
    """Built-in scripts are kept by the app: only admins may change them."""
    if scripts.is_builtin(script.name) and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Built-in scripts can only be changed by admins")


def _name_free(session, name: str, script_id: int | None = None) -> None:
    if scripts.is_builtin(name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Names starting with {scripts.BUILTIN_PREFIX!r} are kept for built-in scripts")
    other = session.scalar(select(Script).where(Script.name == name))
    if other is not None and other.id != script_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a script called {name!r}")


@router.get("", response_model=list[ScriptOut])
def list_scripts(user: Agent, session: SessionDep):
    """Those who may write scripts see every one; others the active ones (can_run says which they may run)."""
    stmt = select(Script).order_by(Script.kind, Script.name)
    if not _manages(session, user):
        stmt = stmt.where(Script.is_active)
    return [_out(s, user) for s in session.scalars(stmt)]


@router.post("", response_model=ScriptOut, status_code=status.HTTP_201_CREATED)
def create_script(body: ScriptIn, admin: ScriptsManager, session: SessionDep):
    params = _checked(body)
    name = body.name.strip()
    _name_free(session, name)
    # Who may run it and whether it needs approval are admins' calls: other writers' scripts start
    # as admin-only and high risk until an admin says otherwise.
    admin_ok = admin.role == UserRole.admin
    script = Script(
        name=name, description=body.description, kind=body.kind, language=body.language, high_risk=body.high_risk if admin_ok else True,
        agents_can_run=body.agents_can_run if admin_ok else False, is_active=body.is_active, current_version=1,
    )
    script.versions.append(ScriptVersion(number=1, body=body.body, language=body.language, parameters=params, note=body.note or "First version", author_id=admin.id))
    _review(_current(script), admin)
    session.add(script)
    session.commit()
    log.info("Script %r created by %s", script.name, admin.display_name, extra={"source": "settings"})
    return _out(script, admin)


@router.get("/{script_id}", response_model=ScriptOut)
def get_script(script_id: int, user: Agent, session: SessionDep):
    return _out(_script(session, script_id, user), user)


@router.put("/{script_id}", response_model=ScriptOut)
def update_script(script_id: int, body: ScriptIn, admin: ScriptsManager, session: SessionDep):
    """Save. A changed body or parameters becomes a new version; the rest is just updated."""
    script = _script(session, script_id, admin, for_update=True)
    _not_builtin(script, admin)
    if body.kind != script.kind:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A script can't change between query and action: make a new one")
    params = _checked(body)
    name = body.name.strip()
    if not (scripts.is_builtin(script.name) and name == script.name):
        _name_free(session, name, script.id)
    current = _current(script)
    executable_changed = body.body != current.body or params != current.parameters or body.language != script.language
    policy_tightened = (script.is_active and not body.is_active) or (script.agents_can_run and not body.agents_can_run) or (not script.high_risk and body.high_risk)
    script.name, script.description, script.language = name, body.description, body.language
    if admin.role == UserRole.admin:
        script.high_risk, script.agents_can_run = body.high_risk, body.agents_can_run
    elif (body.high_risk, body.agents_can_run) != (script.high_risk, script.agents_can_run):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change who may run a script or whether it's high risk")
    script.is_active = body.is_active
    if executable_changed:
        # Script writers cannot inherit execution authorization from older code.
        # An administrator must review and explicitly re-enable technician access.
        if admin.role != UserRole.admin:
            script.high_risk = True
            script.agents_can_run = False
        number = max(v.number for v in script.versions) + 1
        script.versions.append(ScriptVersion(number=number, body=body.body, language=body.language, parameters=params, note=body.note, author_id=admin.id))
        script.current_version = number
        log.info("Script %r saved as version %s by %s", script.name, number, admin.display_name, extra={"source": "settings"})
    if executable_changed:
        runs.cancel_pending(session, script, "Script code changed; request a new run")
    elif policy_tightened:
        runs.recheck_pending(session, script)
    _review(_current(script), admin)
    session.commit()
    return _out(script, admin)


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_script(script_id: int, admin: ScriptsManager, session: SessionDep):
    script = _script(session, script_id, admin)
    _not_builtin(script, admin)
    session.delete(script)
    session.commit()
    log.info("Script %r deleted by %s", script.name, admin.display_name, extra={"source": "settings"})


@router.get("/{script_id}/versions", response_model=list[ScriptVersionOut])
def list_versions(script_id: int, user: Agent, session: SessionDep):
    script = _script(session, script_id, user)
    return [
        ScriptVersionOut(number=v.number, note=v.note, author=UserRef.model_validate(v.author) if v.author else None, created_at=v.created_at, is_current=v.number == script.current_version)
        for v in reversed(script.versions)
    ]


@router.get("/{script_id}/versions/{number}", response_model=ScriptVersionDetailOut)
def get_version(script_id: int, number: int, user: Agent, session: SessionDep):
    script = _script(session, script_id, user)
    by_number = {v.number: v for v in script.versions}
    v = by_number.get(number)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version")
    before = max((n for n in by_number if n < number), default=None)
    diff = scripts.diff(by_number[before].body, v.body, f"version {before}", f"version {number}") if before else ""
    return ScriptVersionDetailOut(
        language=v.language, reviewed_by=UserRef.model_validate(v.reviewed_by) if v.reviewed_by else None, reviewed_at=v.reviewed_at,
        number=v.number, note=v.note, author=UserRef.model_validate(v.author) if v.author else None, created_at=v.created_at,
        is_current=v.number == script.current_version, body=v.body, parameters=[ScriptParam(**p) for p in v.parameters], diff=diff,
    )


@router.post("/{script_id}/versions/{number}/restore", response_model=ScriptOut)
def restore_version(script_id: int, number: int, admin: ScriptsManager, session: SessionDep):
    """Make an old version current again, as a new version (history is never rewritten)."""
    script = _script(session, script_id, admin, for_update=True)
    _not_builtin(script, admin)
    old = next((v for v in script.versions if v.number == number), None)
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version")
    new = max(v.number for v in script.versions) + 1
    language = old.language or script.language
    script.versions.append(ScriptVersion(number=new, body=old.body, language=language, parameters=old.parameters, note=f"Back to version {number}", author_id=admin.id))
    script.language = language
    script.current_version = new
    # Restoring old code also creates a new executable version.
    if admin.role != UserRole.admin:
        script.high_risk = True
        script.agents_can_run = False
    runs.cancel_pending(session, script, "Script version restored; request a new run")
    _review(_current(script), admin)
    session.commit()
    return _out(script, admin)


def _run_query(session, script_id: int, body: ScriptQueryRunIn, user: User) -> engine.ReportResult:
    script = _script(session, script_id, user)
    if script.kind != ScriptKind.query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Actions run on devices through a relay, not here")
    if not _can_run(script, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't run this script")
    v = _current(script)
    try:
        values = scripts.values_for(v.parameters, body.values)
        result = sqlreport.run(v.body, body.date_from, body.date_to, body.limit, values)
    except (scripts.ScriptError, engine.ReportError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    log.info("%s ran query %r (version %s): %s row(s)", user.display_name, script.name, v.number, len(result.rows), extra={"source": "reports"})
    return result


@router.post("/{script_id}/run", response_model=engine.ReportResult)
def run_query(script_id: int, body: ScriptQueryRunIn, user: Agent, session: SessionDep):
    return _run_query(session, script_id, body, user)


@router.post("/{script_id}/run.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def run_query_csv(script_id: int, body: ScriptQueryRunIn, user: Agent, session: SessionDep):
    result = _run_query(session, script_id, body, user)
    return Response(
        content=delivery.csv_bytes(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="query-{script_id}-{date.today().isoformat()}.csv"'},
    )


@router.post("/examples", response_model=list[str])
def add_examples(admin: ScriptsManager, session: SessionDep):
    """Add the example queries that aren't in the library yet. Returns the names added."""
    have = set(session.scalars(select(Script.name)))
    added = []
    admin_ok = admin.role == UserRole.admin
    for ex in scripts.EXAMPLES:
        if ex["name"] in have:
            continue
        script = Script(name=ex["name"], description=ex["description"], kind=ScriptKind.query, language=ScriptLanguage.sql, agents_can_run=admin_ok, current_version=1)
        params = scripts.check_definition(ScriptKind.query, ScriptLanguage.sql, ex["parameters"])
        script.versions.append(ScriptVersion(number=1, language=script.language, body=ex["body"], parameters=params, note="Example", author_id=admin.id))
        _review(_current(script), admin)
        session.add(script)
        added.append(ex["name"])
    session.commit()
    return added
