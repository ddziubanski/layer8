# SPDX-License-Identifier: AGPL-3.0-or-later
"""Full backups: one encrypted file holding the database, the file store (attachments, raw
emails, branding images) and the secrets folder.

Inside the encryption (crypto.py) is a gzipped tar of:

- manifest.json: format, when, from which host and schema revision, and each file's size and
  SHA-256, so a restore can check everything arrived intact;
- database.dump: pg_dump's custom format (all tables, including app_settings);
- storage/... and secrets/...: the two folders as they were.

Backups are written as <name>.partial and renamed once complete and verified, so a share only
ever lists whole backups. Restore is a command on the server (python -m app.cli backup restore),
with the services stopped: it checks the whole file first, then replaces the database and the
two folders, keeping the old folders beside the new ones.
"""

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

from app.backup.crypto import BackupCryptoError, DecryptingReader, EncryptingWriter
from app.config import settings

FORMAT = 1
SUFFIX = ".hdbackup"
PREFIX = "helpdesk-backup-"


class BackupError(ValueError):
    """The message is safe to show."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    path: Path
    size: int
    created_at: datetime


@dataclass(frozen=True)
class Manifest:
    data: dict

    @property
    def created_at(self) -> str:
        return self.data["created_at"]

    @property
    def revision(self) -> str | None:
        return self.data.get("schema_revision")


def _libpq(url: str) -> tuple[str, dict]:
    """A SQLAlchemy URL as a libpq one for pg_dump/pg_restore, with the password in the environment, not the command line."""
    u = make_url(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    return u.set(drivername="postgresql", password=None).render_as_string(hide_password=False), env


def _run(cmd: list[str], env: dict, what: str) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    except FileNotFoundError:
        raise BackupError(f"{cmd[0]} isn't installed on this server (it comes with the PostgreSQL client package)") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise BackupError(f"{what} failed: {detail[-1] if detail else f'exit {done.returncode}'}")
    return done


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _folder_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.name.endswith((".tmp", ".new")) and not p.name.startswith("entra-pending-")
    )


def target_dir() -> Path:
    if not settings.backup_dir:
        raise BackupError("Backups aren't set up: choose a backup folder (Settings > Backup)")
    folder = Path(settings.backup_dir)
    if not folder.is_dir():
        raise BackupError(f"The backup folder {folder} doesn't exist or isn't mounted")
    return folder


def list_backups(folder: Path | None = None) -> list[BackupFile]:
    """Complete backups in the folder, newest first."""
    folder = folder or target_dir()
    out = []
    for p in folder.glob(f"{PREFIX}*{SUFFIX}"):
        st = p.stat()
        out.append(BackupFile(p.name, p, st.st_size, datetime.fromtimestamp(st.st_mtime).astimezone()))
    return sorted(out, key=lambda b: b.name, reverse=True)


def create(session: Session, *, folder: Path | None = None, passphrase: str | None = None, database_url: str | None = None, now: datetime | None = None) -> BackupFile:
    """Write one backup into the folder (verified before it's given its final name)."""
    folder = folder or target_dir()
    passphrase = passphrase if passphrase is not None else settings.backup_passphrase
    if not passphrase:
        raise BackupError("Set a backup passphrase first (Settings > Backup)")
    now = (now or datetime.now()).astimezone()
    final = folder / f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"
    url, env = _libpq(database_url or settings.database_url)
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    counts = {
        name: session.execute(text(f"SELECT count(*) FROM {name}")).scalar()  # noqa: S608 (fixed names)
        for name in ("tickets", "users", "attachments", "inbound_emails")
    }

    # The plain dump only ever sits in a private local temp folder, never on the share.
    with tempfile.TemporaryDirectory(prefix="helpdesk-backup-") as tmp:
        dump = Path(tmp) / "database.dump"
        _run(["pg_dump", "--format=custom", "--no-owner", f"--file={dump}", url], env, "Database dump")
        members: list[tuple[str, Path]] = [("database.dump", dump)]
        members += [(f"storage/{p.relative_to(settings.storage_dir).as_posix()}", p) for p in _folder_files(settings.storage_dir)]
        members += [(f"secrets/{p.relative_to(settings.secrets_dir).as_posix()}", p) for p in _folder_files(settings.secrets_dir)]
        manifest = {"schema_revision": revision, "counts": counts}
        write_archive(final, passphrase, "backup", manifest, members, now)
    st = final.stat()
    return BackupFile(final.name, final, st.st_size, now)


def write_archive(final: Path, passphrase: str, kind: str, manifest: dict, members: list[tuple[str, Path]], now: datetime) -> None:
    """Write the encrypted archive as <final>.partial, read it all back, then give it its final name."""
    partial = final.with_name(final.name + ".partial")
    manifest = {
        "format": FORMAT,
        "app": "helpdesk",
        "kind": kind,
        "created_at": now.isoformat(),
        "host": socket.gethostname(),
        **manifest,
        "files": {name: {"size": p.stat().st_size, "sha256": _sha256(p)} for name, p in members},
    }
    try:
        with partial.open("wb") as out:
            enc = EncryptingWriter(out, passphrase)
            with tarfile.open(fileobj=enc, mode="w|gz") as tar:
                body = json.dumps(manifest, indent=1).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mtime, info.mode = len(body), int(now.timestamp()), 0o600
                tar.addfile(info, io.BytesIO(body))
                for name, p in members:
                    info = tar.gettarinfo(str(p), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with p.open("rb") as f:
                        tar.addfile(info, f)
            enc.close()
            out.flush()
            os.fsync(out.fileno())
        read_archive(partial, passphrase, kind)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _open(path: Path, passphrase: str):
    f = path.open("rb")
    try:
        reader = io.BufferedReader(DecryptingReader(f, passphrase), buffer_size=1024 * 1024)
    except BaseException:
        f.close()
        raise
    return f, tarfile.open(fileobj=reader, mode="r|gz")


# What each kind of archive may hold (top-level names), and must.
CONTENTS = {"backup": ({"database.dump", "storage", "secrets"}, "database.dump"), "export": ({"tables", "storage"}, None)}
KIND_NAMES = {"backup": "a backup", "export": "an export"}


def _check_name(name: str, kind: str) -> None:
    parts = Path(name).parts
    if name.startswith("/") or ".." in parts or not parts or (parts[0] != "manifest.json" and parts[0] not in CONTENTS[kind][0]):
        raise BackupError(f"The file holds an unexpected entry: {name!r}")


def read_archive(path: Path, passphrase: str, kind: str, extract_to: Path | None = None) -> Manifest:
    """Read every member, checking sizes and checksums against the manifest; extract if asked."""
    try:
        return _walk(path, passphrase, kind, extract_to)
    except BackupCryptoError as exc:
        raise BackupError(str(exc)) from None


def _walk(path: Path, passphrase: str, kind: str, extract_to: Path | None) -> Manifest:
    manifest: dict | None = None
    seen: set[str] = set()
    f, tar = _open(path, passphrase)
    try:
        with f, tar:
            for member in tar:
                _check_name(member.name, kind)
                if not member.isfile():
                    raise BackupError(f"The backup holds an unexpected entry: {member.name!r}")
                src = tar.extractfile(member)
                if member.name == "manifest.json":
                    manifest = json.loads(src.read())
                    if manifest.get("app") != "helpdesk" or manifest.get("format") != FORMAT:
                        raise BackupError("Not a file this version can read")
                    if manifest.get("kind", "backup") != kind:
                        raise BackupError(f"This is {KIND_NAMES.get(manifest.get('kind'), 'something else')}, not {KIND_NAMES[kind]}")
                    continue
                if manifest is None:
                    raise BackupError("The backup has no manifest")
                expected = manifest["files"].get(member.name)
                if expected is None:
                    raise BackupError(f"{member.name} isn't listed in the backup's manifest")
                h, size = hashlib.sha256(), 0
                out = None
                if extract_to is not None:
                    target = extract_to / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    out = target.open("wb")
                try:
                    while chunk := src.read(1024 * 1024):
                        h.update(chunk)
                        size += len(chunk)
                        if out:
                            out.write(chunk)
                finally:
                    if out:
                        out.close()
                if size != expected["size"] or h.hexdigest() != expected["sha256"]:
                    raise BackupError(f"{member.name} is damaged in the backup")
                seen.add(member.name)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise BackupError(f"The backup can't be read: {exc}") from None
    except json.JSONDecodeError:
        raise BackupError("The backup's manifest is damaged") from None
    if manifest is None:
        raise BackupError("The backup has no manifest")
    if missing := set(manifest["files"]) - seen:
        raise BackupError(f"The backup is missing {len(missing)} file(s), e.g. {sorted(missing)[0]}")
    if (required := CONTENTS[kind][1]) and required not in seen:
        raise BackupError(f"The backup has no {required}")
    return Manifest(manifest)


def verify(path: Path, passphrase: str | None = None) -> Manifest:
    """Decrypt and check every byte of a backup (nothing is written). BackupError if it's not whole."""
    passphrase = passphrase if passphrase is not None else settings.backup_passphrase
    return read_archive(path, passphrase, "backup")


def prune(folder: Path, keep: int) -> list[str]:
    """Delete all but the newest `keep` backups (and leftovers of interrupted ones). Returns what was removed."""
    removed = []
    for b in list_backups(folder)[keep:]:
        b.path.unlink(missing_ok=True)
        removed.append(b.name)
    for p in folder.glob(f"{PREFIX}*{SUFFIX}.partial"):
        # A partial file older than a day is from a backup that died.
        if datetime.now().timestamp() - p.stat().st_mtime > 86400:
            p.unlink(missing_ok=True)
            removed.append(p.name)
    return removed


def _swap_folder(new: Path, current: Path, stamp: str) -> Path | None:
    """Put `new` where `current` is; the old one is kept as <current>.before-restore-<stamp>."""
    kept = None
    if current.exists():
        kept = current.with_name(f"{current.name}.before-restore-{stamp}")
        os.replace(current, kept)
    current.parent.mkdir(parents=True, exist_ok=True)
    if new.exists():
        shutil.move(str(new), str(current))
    else:
        current.mkdir()
    if current == settings.secrets_dir:
        current.chmod(0o700)
        for p in current.rglob("*"):
            if p.is_file():
                p.chmod(0o600)
    return kept


def restore(path: Path, passphrase: str, *, database_url: str | None = None, replace_public_schema: bool = False) -> tuple[Manifest, list[str]]:
    """Replace the database, file store and secrets with the backup's. The services must be stopped.

    Everything is checked before anything is changed; the database is restored in one
    transaction. Returns the manifest and what was done.
    """
    url, env = _libpq(database_url or settings.database_url)
    # Unpack next to the file store, so the folders can be moved into place without copying.
    work_root = settings.storage_dir.resolve().parent
    work_root.mkdir(parents=True, exist_ok=True)
    done = []
    with tempfile.TemporaryDirectory(prefix=".helpdesk-restore-", dir=work_root) as tmp:
        work = Path(tmp)
        os.chmod(work, 0o700)
        manifest = read_archive(path, passphrase, "backup", work)
        _run(["pg_restore", "--list", str(work / "database.dump")], env, "Reading the database dump")
        if replace_public_schema:
            # Updater rollback can follow a migration that introduced tables/FKs
            # absent from the old dump. pg_restore --clean alone leaves those
            # behind (or fails dropping referenced old tables). Reset and restore
            # the dedicated app's public schema in ONE transaction instead.
            sql = work / "database.sql"
            _run(["pg_restore", "--no-owner", f"--file={sql}", str(work / "database.dump")], env, "Preparing database rollback")
            prepared = work / "rollback.sql"
            # PostgreSQL versions differ on whether the standard public schema
            # has an explicit CREATE statement in a dump.
            creates_public = False
            with sql.open("r", encoding="utf-8") as source:
                for line in source:
                    if line.strip() in ('CREATE SCHEMA public;', 'CREATE SCHEMA "public";'):
                        creates_public = True
                        break
            with prepared.open("wb") as output, sql.open("rb") as source:
                output.write(b"DROP SCHEMA IF EXISTS public CASCADE;\n")
                if not creates_public:
                    output.write(b"CREATE SCHEMA public;\n")
                shutil.copyfileobj(source, output)
            _run(["psql", "--no-psqlrc", "--single-transaction", "--set=ON_ERROR_STOP=1", f"--dbname={url}", f"--file={prepared}"], env, "Atomic database rollback")
        else:
            _run(
                ["pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction", "--exit-on-error", f"--dbname={url}", str(work / "database.dump")],
                env,
                "Database restore",
            )
        done.append(f"Database restored (schema revision {manifest.revision})")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for name, current in (("storage", settings.storage_dir.resolve()), ("secrets", settings.secrets_dir.resolve())):
            kept = _swap_folder(work / name, current, stamp)
            count = sum(1 for f in manifest.data["files"] if f.startswith(f"{name}/"))
            done.append(f"{name.capitalize()}: {count} file(s) restored to {current}" + (f"; the previous folder is kept as {kept}" if kept else ""))
    return manifest, done
