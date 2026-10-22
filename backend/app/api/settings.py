"""Settings admins change in the app: Windows sign-in, and the general/directory/... sections."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import sessionmaker

from app import configstore, jobs, kerberos, sla
from app.backup import archive
from app.config import settings
from app.auth import Admin, SessionDep
from app.db import get_session_factory

log = logging.getLogger(__name__)
from app.models import WorkerJob
from app.schemas import BackupFileOut, BackupListOut, BackupVerifyOut, EmailTestOut, MailPassItemOut, MailPassOut, SettingsSectionOut, SettingsUpdateIn, WindowsSignInIn, WindowsSignInOut

router = APIRouter(prefix="/api/settings", tags=["settings"])


def windows_out(config: kerberos.WindowsSignIn) -> WindowsSignInOut:
    has_keytab = bool(config.keytab_principals)
    problem = None
    if has_keytab:
        problem = kerberos.keytab_problem() if kerberos.keytab_path().exists() else "The keytab file is missing from the server; upload it again"
    return WindowsSignInOut(
        enabled=config.enabled,
        silent=config.silent,
        realm=config.realm,
        keytab_principals=config.keytab_principals,
        keytab_enctypes=config.keytab_enctypes,
        keytab_uploaded_at=config.keytab_uploaded_at,
        keytab_uploaded_by=config.keytab_uploaded_by,
        problem=problem,
    )


@router.get("/windows-sign-in", response_model=WindowsSignInOut)
def get_windows_sign_in(_: Admin, session: SessionDep):
    return windows_out(kerberos.load(session))


@router.put("/windows-sign-in", response_model=WindowsSignInOut)
def update_windows_sign_in(body: WindowsSignInIn, admin: Admin, session: SessionDep):
    config = kerberos.load(session)
    if body.enabled and not (config.keytab_principals and kerberos.keytab_path().is_file()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Upload the portal's keytab before turning Windows sign-in on")
    config.enabled, config.silent = body.enabled, body.silent
    kerberos.save(session, config, admin)
    session.commit()
    log.info("Windows sign-in %s%s by %s", "on" if config.enabled else "off", ", automatic" if config.enabled and config.silent else "", admin.display_name, extra={"source": "windows_sign_in"})
    return windows_out(config)


@router.put("/windows-sign-in/keytab", response_model=WindowsSignInOut)
def upload_keytab(admin: Admin, session: SessionDep, file: UploadFile = File(...)):
    data = file.file.read(kerberos.MAX_KEYTAB_BYTES + 1)
    try:
        config = kerberos.install_keytab(session, data, admin)
    except kerberos.KeytabError as exc:
        log.warning("Keytab refused (%s): %s", admin.display_name, exc, extra={"source": "windows_sign_in"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    log.info("Keytab uploaded by %s: %s", admin.display_name, ", ".join(config.keytab_principals), extra={"source": "windows_sign_in"})
    return windows_out(config)


@router.delete("/windows-sign-in/keytab", response_model=WindowsSignInOut)
def delete_keytab(admin: Admin, session: SessionDep):
    config = kerberos.remove_keytab(session, admin)
    session.commit()
    log.warning("Keytab removed by %s: Windows sign-in is off", admin.display_name, extra={"source": "windows_sign_in"})
    return windows_out(config)


@router.post("/email/test", response_model=EmailTestOut)
def test_email(admin: Admin):
    """Sign in to Graph with the saved settings and read the mailbox's Inbox. Sends nothing."""
    from app.config import settings
    from app.mail import graph

    if not settings.mailbox:
        raise HTTPException(status.HTTP_409_CONFLICT, "Set the service desk mailbox first")
    if graph.missing(settings):
        raise HTTPException(status.HTTP_409_CONFLICT, "Still needed: " + ", ".join(graph.missing(settings)))
    try:
        result = graph.from_settings(settings).check()
    except Exception as exc:  # MSAL, network and Graph errors all go back to the admin as text
        log.warning("Email connection test failed: %s", str(exc)[:500], extra={"source": "email"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)[:500]) from None
    log.info("Email connection test: %s OK", result["mailbox"], extra={"source": "email"})
    return result


@router.post("/email/check-now", response_model=MailPassOut)
def check_mailbox_now(_: Admin, factory: Annotated[sessionmaker, Depends(get_session_factory)]):
    """One mail pass now, as the worker does on its timer: read the Inbox, then send queued email."""
    from app.config import settings
    from app.mail import graph, runner
    from app.mail.intake import IntakeConfig

    if not settings.mailbox or graph.missing(settings):
        raise HTTPException(status.HTTP_409_CONFLICT, "Finish the mailbox settings first")
    try:
        result = runner.run_pass(factory, graph.from_settings(settings), IntakeConfig.create(settings.mailbox, settings.mail_allowed_domains))
    except Exception as exc:  # sign-in, network and Graph errors go back to the admin as text
        log.warning("Check mailbox now failed: %s", str(exc)[:500], extra={"source": "email"})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)[:500]) from None
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The mailbox is being checked right now; try again in a moment")
    count = lambda s: sum(o.status == s for o in result.outcomes)  # noqa: E731
    log.info("Check mailbox now: %s processed, %s rejected, %s failed, %s sent", count("processed"), count("rejected"), count("failed"), result.sent, extra={"source": "email"})
    return MailPassOut(
        processed=count("processed"),
        rejected=count("rejected"),
        failed=count("failed"),
        sent=result.sent,
        items=[MailPassItemOut(status=o.status, ticket_id=o.ticket_id, reason=o.reason) for o in result.outcomes],
    )


# Declared after the Windows sign-in routes, which this would otherwise match.


def _section(section: str) -> SettingsSectionOut:
    if section not in configstore.SECTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such settings section")
    return SettingsSectionOut(section=section, fields=configstore.describe(section))


@router.get("/{section}", response_model=SettingsSectionOut)
def get_section(section: str, _: Admin):
    return _section(section)


@router.put("/{section}", response_model=SettingsSectionOut)
def update_section(section: str, body: SettingsUpdateIn, admin: Admin, session: SessionDep):
    _section(section)
    try:
        configstore.update(session, section, body.values, admin, body.clear)
    except configstore.SettingsError as exc:
        session.rollback()
        configstore.refresh(session, force=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if section == "backup":
        # A new time applies from the next backup on.
        row = session.get(WorkerJob, "backup")
        if row is not None:
            row.next_due_at = sla.utcnow() + jobs.until_backup()
    session.commit()
    return _section(section)


# Backups (the settings themselves are the "backup" section)


@router.get("/backup/files", response_model=BackupListOut)
def list_backup_files(_: Admin):
    """The backups in the backup folder, newest first, or why they can't be listed."""
    try:
        files = archive.list_backups()
    except (archive.BackupError, OSError) as exc:
        return BackupListOut(folder=settings.backup_dir, files=[], problem=str(exc))
    return BackupListOut(folder=settings.backup_dir, files=[BackupFileOut(name=b.name, size=b.size, created_at=b.created_at) for b in files])


@router.post("/backup/files/{name}/verify", response_model=BackupVerifyOut)
def verify_backup_file(name: str, admin: Admin):
    """Decrypt and check every byte of one backup. Takes a while for a big one."""
    try:
        found = next((b for b in archive.list_backups() if b.name == name), None)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such backup")
        manifest = archive.verify(found.path)
    except (archive.BackupError, OSError) as exc:
        log.warning("Backup %s failed verification (asked by %s): %s", name, admin.display_name, exc, extra={"source": "backup"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    log.info("Backup %s verified by %s", name, admin.display_name, extra={"source": "backup"})
    return BackupVerifyOut(
        name=name,
        created_at=manifest.created_at,
        host=manifest.data.get("host", ""),
        schema_revision=manifest.revision,
        files=len(manifest.data["files"]),
        counts=manifest.data.get("counts", {}),
    )
