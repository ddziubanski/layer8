"""Export and import: move configuration and data from one install to another (a new server, a
test copy), across app versions.

Unlike a backup, an export holds no secret files (the directory password, Graph client secret
and keytab are entered again on the new install; local accounts' password hashes do move) and nothing tied to the old server (sessions, heartbeats, worker
state, the system log). Each table is copied out with COPY as CSV, ordered by key, and the
file store goes with it, in the same encrypted wrapper as backups.

Import goes into an install without tickets. It replaces every table in the export, loading
only the columns both installs have, so an export from an older version still loads (a newer
column takes its default). Everything happens in one transaction, which a dry run rolls back.
Email that was still waiting to be sent is marked as not sent, so a test copy can't mail people.
"""

import csv
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.backup import archive
from app.backup.archive import BackupError
from app.config import settings
from app.models import Base

SUFFIX = ".hdexport"
# Tied to one server, or rebuilt by it: never moved.
SKIPPED = {"alembic_version", "user_sessions", "service_heartbeats", "service_requests", "worker_jobs", "system_events"}


@dataclass
class ImportResult:
    tables: dict[str, int] = field(default_factory=dict)
    files: int = 0
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(session: Session) -> dict[str, list[str]]:
    """Tables in the database and their columns, in column order. Generated columns (a knowledge base
    article's search index) are left out: the database makes them again from the rest."""
    rows = session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND is_generated = 'NEVER' "
            "ORDER BY table_name, ordinal_position"
        )
    ).all()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


def _order(names) -> list[str]:
    """Parents before children (as the app's models declare them), then any the code doesn't know."""
    known = [t.name for t in Base.metadata.sorted_tables if t.name in names]
    return known + sorted(set(names) - set(known))


def _primary_key(table: str) -> list[str]:
    t = Base.metadata.tables.get(table)
    return [c.name for c in t.primary_key] if t is not None else []


def _raw(session: Session):
    """The psycopg connection under the session, for COPY."""
    return session.connection().connection.driver_connection


def export(session: Session, out: Path, passphrase: str, now: datetime | None = None) -> archive.Manifest:
    if not passphrase:
        raise BackupError("An export needs a passphrase (it holds people's details and tickets)")
    now = (now or datetime.now()).astimezone()
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    tables = {t: cols for t, cols in _tables(session).items() if t not in SKIPPED}
    raw = _raw(session)
    manifest_tables = {}
    with tempfile.TemporaryDirectory(prefix="helpdesk-export-") as tmp:
        members = []
        for table in _order(tables):
            cols = tables[table]
            order = ", ".join(_q(c) for c in _primary_key(table)) or "1"
            path = Path(tmp) / f"{table}.csv"
            rows = 0
            with path.open("wb") as f, raw.cursor() as cur:
                select = f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(table)} ORDER BY {order}"
                with cur.copy(f"COPY ({select}) TO STDOUT (FORMAT csv, HEADER)") as copy:
                    for block in copy:
                        f.write(block)
                rows = cur.rowcount
            manifest_tables[table] = {"columns": cols, "rows": rows}
            members.append((f"tables/{table}.csv", path))
        members += [(f"storage/{p.relative_to(settings.storage_dir).as_posix()}", p) for p in archive._folder_files(settings.storage_dir)]
        archive.write_archive(out, passphrase, "export", {"schema_revision": revision, "tables": manifest_tables}, members, now)
    return archive.read_archive(out, passphrase, "export")


def import_(session: Session, path: Path, passphrase: str, *, dry_run: bool, known_revisions: set[str]) -> ImportResult:
    """Load an export into this install (which must have no tickets). The caller commits, unless dry_run."""
    result = ImportResult(dry_run=dry_run)
    with tempfile.TemporaryDirectory(prefix=".helpdesk-import-", dir=settings.storage_dir.resolve().parent) as tmp:
        work = Path(tmp)
        manifest = archive.read_archive(path, passphrase, "export", work)
        if manifest.revision not in known_revisions:
            raise BackupError(f"The export comes from a newer version (schema {manifest.revision}): update this install first")
        if session.execute(text("SELECT EXISTS (SELECT 1 FROM tickets)")).scalar():
            raise BackupError("This install already has tickets: import only goes into a new, empty install")

        here = {t: cols for t, cols in _tables(session).items() if t not in SKIPPED}
        theirs: dict[str, dict] = manifest.data["tables"]
        for table in sorted(set(theirs) - set(here)):
            if theirs[table]["rows"]:
                result.notes.append(f"Skipped {table} ({theirs[table]['rows']} rows): this version doesn't have it")
        loading = [t for t in _order(here) if t in theirs]
        session.execute(text(f"TRUNCATE {', '.join(_q(t) for t in loading)} RESTART IDENTITY CASCADE"))
        raw = _raw(session)
        for table in loading:
            cols = [c for c in theirs[table]["columns"] if c in here[table]]
            dropped = [c for c in theirs[table]["columns"] if c not in here[table]]
            if dropped:
                result.notes.append(f"{table}: left out {', '.join(dropped)} (not in this version)")
            # Load through a staging copy, so only shared columns are taken and rows that refer to
            # rows of the same table (an escalation queue) can come in any order.
            stage = f"import_{table}"
            session.execute(text(f"CREATE TEMP TABLE {_q(stage)} (LIKE {_q(table)} INCLUDING DEFAULTS) ON COMMIT DROP"))
            for c in dropped:
                # Somewhere to put what this version has no column for; it isn't kept.
                session.execute(text(f"ALTER TABLE {_q(stage)} ADD COLUMN {_q(c)} text"))
            source = work / "tables" / f"{table}.csv"
            with source.open("rb") as f, raw.cursor() as cur:
                header = next(csv.reader([f.readline().decode()]))
                f.seek(0)
                with cur.copy(f"COPY {_q(stage)} ({', '.join(_q(c) for c in header)}) FROM STDIN (FORMAT csv, HEADER MATCH)") as copy:
                    while block := f.read(1024 * 1024):
                        copy.write(block)
            self_refs = _self_references(table)
            col_list = ", ".join(_q(c) for c in cols)
            first = ", ".join("NULL" if c in self_refs else _q(c) for c in cols)
            session.execute(text(f"INSERT INTO {_q(table)} ({col_list}) SELECT {first} FROM {_q(stage)}"))
            for c in self_refs:
                if c in cols:
                    pk = _primary_key(table)
                    match = " AND ".join(f"t.{_q(k)} = s.{_q(k)}" for k in pk)
                    session.execute(text(f"UPDATE {_q(table)} t SET {_q(c)} = s.{_q(c)} FROM {_q(stage)} s WHERE {match} AND s.{_q(c)} IS NOT NULL"))
            result.tables[table] = session.execute(text(f"SELECT count(*) FROM {_q(table)}")).scalar()
            # Carry on numbering after the imported rows.
            if "id" in here[table] and (seq := session.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}).scalar()):
                session.execute(text(f"SELECT setval(:seq, coalesce((SELECT max(id) FROM {_q(table)}), 0) + 1, false)"), {"seq": seq})
        held = session.execute(text("UPDATE outbound_emails SET status = 'failed', last_error = 'Not sent: imported from another install' WHERE status = 'queued'")).rowcount
        if held:
            result.notes.append(f"{held} email(s) that were still waiting to be sent on the old install were marked as not sent")
        files = sorted((work / "storage").rglob("*")) if (work / "storage").is_dir() else []
        files = [p for p in files if p.is_file()]
        result.files = len(files)
        if not dry_run:
            for p in files:
                dest = settings.storage_dir / p.relative_to(work / "storage")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
    return result


def _self_references(table: str) -> set[str]:
    t = Base.metadata.tables.get(table)
    if t is None:
        return set()
    return {fk.parent.name for fk in t.foreign_keys if fk.column.table is t}
