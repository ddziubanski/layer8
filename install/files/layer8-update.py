#!/usr/bin/python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed root service entrypoint; never imports code from a web-writable directory.

Installed with protocol.py in /usr/local/lib/layer8-updater. No arguments or
environment overrides select paths, commands, repository, key or service names.
"""
import fcntl
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, "/usr/local/lib/layer8-updater")
import protocol

APP = Path("/opt/helpdesk")
STATE = Path("/var/lib/layer8-updater")
ENV = Path("/etc/helpdesk/helpdesk.env")
SERVICES = ["helpdesk-api", "helpdesk-worker"]


def run(command, *, cwd=None, env=None, timeout=1800):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    # Full details are for OS administrators only, never the capability endpoint.
    # Keep bounded output locally so a failed migration/dependency step is diagnosable.
    path = STATE / "details.log"
    if path.exists() and path.stat().st_size > 10 * 1024 * 1024:
        path.replace(STATE / "details.log.1")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(f"\n{datetime.now(timezone.utc).isoformat()} {Path(command[0]).name} exit={result.returncode}\n".encode())
        output.write((result.stdout or b"")[-65536:])
        output.write((result.stderr or b"")[-65536:])
    if result.returncode:
        # Logs of migrations/pip/backups can contain site data. Do not copy to web status.
        raise protocol.UpdateError(f"{Path(command[0]).name} step failed; services remain protected until recovery finishes")
    return result


def secure(path: Path, *, directory=False):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode)):
        raise protocol.UpdateError("Updater files and trust keys must be root-owned and not writable by other users")


def installed_version() -> str:
    path = APP / "app/app/__init__.py"
    secure(APP, directory=True)
    secure(path)
    match = re.search(r'^VERSION = "([^"]+)"$', path.read_text(), re.M)
    if not match:
        raise protocol.UpdateError("Could not identify the installed application version")
    protocol.version(match[1])
    return match[1]


def supported_platform():
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    return (platform.machine() in ("x86_64", "amd64") and sys.version_info[:2] == (3, 13)
            and release.get("ID", "").strip('"') == "debian" and release.get("VERSION_ID", "").strip('"') == "13")


def read_request() -> dict:
    fd = os.open(STATE / "inbox/request.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise protocol.UpdateError("Update request must be a regular file")
        data = source.read(4097)
    try:
        request = json.loads(data)
        if len(data) > 4096 or set(request) != {"version", "manifest_sha256", "requested_by", "id"}:
            raise ValueError
        protocol.version(request["version"])
        if not re.fullmatch("[0-9a-f]{64}", request["manifest_sha256"]) or not re.fullmatch("[0-9a-f]{32}", request["id"]) or type(request["requested_by"]) is not int:
            raise ValueError
        return request
    except (ValueError, TypeError, KeyError):
        raise protocol.UpdateError("Invalid update request") from None


def unpack(package: Path, destination: Path) -> int:
    """Only regular files/directories; reject traversal, links, duplicates and archive bombs."""
    total, seen = 0, set()
    allowed = {"backend", "frontend", "wheels", "runner", "tools", "docs", "LICENSE", "layer8-source.tar.gz", "VERSION"}
    try:
        with tarfile.open(package, "r:gz") as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if not parts or member.name.startswith("/") or ".." in parts or "\\" in member.name or parts[0] not in allowed or member.name in seen:
                    raise protocol.UpdateError("Unsafe path or duplicate in update package")
                if not member.isdir() and not member.isfile():
                    raise protocol.UpdateError("Update packages cannot contain links, devices or special files")
                seen.add(member.name)
                total += member.size
                if len(seen) > 100000 or total > protocol.MAX_EXPANDED:
                    raise protocol.UpdateError("Expanded update package exceeds limits")
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
        # Archives need not list parent directories explicitly. Every extracted
        # application directory must be traversable by the unprivileged service.
        for directory in destination.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
    except (tarfile.TarError, OSError, EOFError):
        raise protocol.UpdateError("The update package could not be extracted safely") from None
    required = ["backend/app/__init__.py", "backend/alembic.ini", "frontend/dist/index.html", "layer8-source.tar.gz", "LICENSE", "VERSION"]
    if any(not (destination / name).is_file() for name in required) or not list((destination / "wheels").glob("helpdesk-*.whl")):
        raise protocol.UpdateError("The update is missing offline application files, wheels or its source archive")
    return total


def app_env() -> dict:
    secure(ENV)
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONNOUSERSITE": "1"}
    for line in ENV.read_text().splitlines():
        if line.startswith("HELPDESK_") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    return env


def as_app(arguments: list[str], *, python=False):
    executable = APP / ("venv/bin/python" if python else "venv/bin/alembic")
    return run(["/usr/sbin/runuser", "-u", "helpdesk", "--", str(executable), *arguments], cwd=APP / "app", env=app_env())


def health(expected: str):
    # The health endpoint alone doesn't exercise the DB. Check both, without opening Nginx.
    as_app(["-c", "from app.db import engine; from sqlalchemy import text; c=engine.connect(); c.execute(text('SELECT 1')); c.close()"], python=True)
    for _ in range(30):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:8000/api/about", timeout=2) as response:
                data = json.loads(response.read(16384))
                if data.get("version") == expected:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise protocol.UpdateError("Updated application did not pass its health check")


def database_report():
    result = as_app(["-c", "import json,importlib.metadata; from sqlalchemy import text; from app.db import engine; from app import VERSION; from alembic.config import Config; from alembic.script import ScriptDirectory; c=engine.connect(); print(json.dumps({'version':VERSION,'wheel':importlib.metadata.version('helpdesk'),'heads':ScriptDirectory.from_config(Config('alembic.ini')).get_heads(),'revisions':list(c.execute(text('SELECT version_num FROM alembic_version')).scalars()),'counts':{t:c.execute(text('SELECT count(*) FROM '+t)).scalar() for t in ('tickets','comments','attachments','users')}})); c.close()"], python=True)
    return json.loads(result.stdout)


def worker_ready(expected: str, since: str):
    for _ in range(30):
        result = as_app(["-c", "import json; from app.db import SessionLocal; from app.models import ServiceHeartbeat; s=SessionLocal(); b=s.get(ServiceHeartbeat,'worker'); print(json.dumps({'last_seen':b.last_seen.isoformat(),'started_at':b.info.get('started_at'),'version':b.info.get('version'),'pid':b.info.get('pid')} if b else {})); s.close()"], python=True)
        info = json.loads(result.stdout)
        if (info.get("version") == expected and info.get("started_at") and info.get("last_seen")
                and datetime.fromisoformat(info["started_at"]) >= datetime.fromisoformat(since)
                and datetime.fromisoformat(info["last_seen"]) >= datetime.fromisoformat(since)):
            pid = run(["/usr/bin/systemctl", "show", "--property=MainPID", "--value", "helpdesk-worker"], timeout=15).stdout.strip()
            if pid != str(info.get("pid")).encode():
                raise protocol.UpdateError("Worker heartbeat does not belong to the active service process")
            time.sleep(10)
            run(["/usr/bin/systemctl", "is-active", "--quiet", *SERVICES], timeout=15)
            if run(["/usr/bin/systemctl", "show", "--property=MainPID", "--value", "helpdesk-worker"], timeout=15).stdout.strip() != pid:
                raise protocol.UpdateError("Worker restarted during its stability check")
            return
        time.sleep(2)
    raise protocol.UpdateError("The updated worker did not publish a fresh startup heartbeat")


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def http_hash(url, maximum=protocol.MAX_PACKAGE):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    digest, count = hashlib.sha256(), 0
    with opener.open(url, timeout=10) as response:
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > maximum:
                raise protocol.UpdateError("Application response exceeds its expected limit")
            digest.update(chunk)
    return digest.hexdigest()


class Updater:
    def __init__(self, request):
        self.request = request
        self.work = STATE / "work" / request["id"]
        self.previous = Path("/opt") / f"helpdesk-previous-{request['id']}"
        self.staged = Path("/opt") / f".helpdesk-update-{request['id']}"
        self.backup = None
        self.stopped = self.swapped = self.migration = self.committed = False
        self.old_version = ""
        self.started = datetime.now(timezone.utc).isoformat()
        self.checks, self.events, self.hashes = [], [], {}
        self.download = None
        self.baseline = None
        self.phase = "queued"

    def status(self, phase, message):
        self.phase = phase
        steps = {"verifying": 1, "downloading": 2, "backing_up": 3, "installing": 4, "migrating": 5, "checking": 6, "starting": 7, "worker_check": 8, "succeeded": 9}
        now = datetime.now(timezone.utc).isoformat()
        if not self.events or self.events[-1]["message"] != message:
            self.events.append({"at": now, "message": message})
        public = {"phase": phase, "message": message, "version": self.request["version"], "started_at": self.started, "updated_at": now,
                  "step": steps.get(phase), "steps": 9, "checks": self.checks, "events": self.events[-100:], "download": self.download}
        body = {**public, "request_id": self.request["id"], "requested_by": self.request["requested_by"],
                "updated_at": datetime.now(timezone.utc).isoformat(), "previous": str(self.previous), "backup": str(self.backup) if self.backup else None}
        temporary = STATE / "status.tmp"
        with temporary.open("w") as out:
            json.dump(body, out)
            out.flush()
            os.fsync(out.fileno())
        temporary.chmod(0o644)
        temporary.replace(STATE / "status.json")
        # Capability URL shared only by the admin API. No paths, users, counts or
        # raw command output are published. Nginx can serve this while API is down.
        progress = STATE / "progress" / (self.request["id"] + ".json")
        temporary = progress.with_suffix(".tmp")
        with temporary.open("w") as out:
            json.dump(public, out)
            out.flush()
            os.fsync(out.fileno())
        temporary.chmod(0o644)
        temporary.replace(progress)

    def checked(self, name, detail):
        self.checks.append({"name": name, "state": "passed", "detail": detail})
        self.status("checking" if self.phase == "migrating" else self.phase, detail)

    def prepare(self):
        self.status("verifying", "Checking the reviewed publisher signature and package")
        self.old_version = installed_version()
        gate = Path("/etc/nginx/sites-available/helpdesk")
        secure(gate)
        if "/var/lib/layer8-updater/maintenance" not in gate.read_text():
            raise protocol.UpdateError("The appliance maintenance gate is not installed")
        as_app(["-c", "from app import configstore; from app.db import SessionLocal; from app.config import settings; s=SessionLocal(); configstore.refresh(s, force=True); assert settings.updates_enabled and settings.backup_passphrase, 'Enable updates and configure a backup passphrase first'"], python=True)
        if not supported_platform():
            raise protocol.UpdateError("Updates support the Debian 13 amd64 appliance with Python 3.13")
        secure(protocol.KEYS, directory=True)
        for key in protocol.KEYS.glob("*.pem"):
            secure(key)
        published = json.loads(protocol.fetch(protocol.API + "/tags/" + protocol.tag(self.request["version"])))
        if published.get("draft") is not False or published.get("prerelease") is not False or published.get("tag_name") != protocol.tag(self.request["version"]):
            raise protocol.UpdateError("Only published stable GitHub releases may be installed")
        manifest, digest = protocol.signed_release(self.request["version"])
        if digest != self.request["manifest_sha256"]:
            raise protocol.UpdateError("Release changed since review; check and review it again")
        protocol.compatible(manifest, self.old_version)
        self.checked("signature", "Publisher signature and reviewed manifest verified")
        self.work.mkdir(parents=True, mode=0o700)
        package = self.work / "package.tar.gz"
        last_progress = [0.0]
        def downloaded(count, total):
            if time.monotonic() - last_progress[0] >= 1 or count == total:
                self.download = {"received": count, "total": total}
                self.status("downloading", "Downloading the signed application bundle")
                last_progress[0] = time.monotonic()
        self.status("downloading", "Downloading the signed application bundle")
        protocol.fetch(protocol.asset_url(self.request["version"], manifest["package"]), manifest["size"], package, progress=downloaded)
        with package.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if package.stat().st_size != manifest["size"] or digest != manifest["sha256"]:
            raise protocol.UpdateError("Update package checksum or size does not match its signed manifest")
        self.checked("package", "Downloaded package size and SHA-256 verified")
        # Bound extraction itself by free space too, before writing to the application filesystem.
        if shutil.disk_usage(APP.parent).free < protocol.MAX_EXPANDED + 512 * 1024 * 1024:
            raise protocol.UpdateError("At least 4.5 GiB free on the application disk is needed")
        self.staged.mkdir(mode=0o755)
        self.staged.chmod(0o755)
        bundle = self.staged / "bundle"
        bundle.mkdir()
        unpack(package, bundle)
        if bundle.joinpath("VERSION").read_text().strip().removeprefix("v") != manifest["version"].removeprefix("v"):
            raise protocol.UpdateError("Package version does not match the signed manifest")
        source = bundle.joinpath("backend/app/__init__.py").read_text()
        if not re.search(r'^VERSION = "' + re.escape(manifest["version"].removeprefix("v")) + r'"$', source, re.M):
            raise protocol.UpdateError("Application version does not match the signed manifest")
        with tarfile.open(bundle / "layer8-source.tar.gz", "r:gz") as source_archive:
            found = False
            for index, member in enumerate(source_archive):
                if index > 100000:
                    break
                if member.isfile() and member.name.endswith("/backend/app/__init__.py") and member.size < 65536:
                    source_text = source_archive.extractfile(member).read().decode("utf-8")
                    found = bool(re.search(r'^VERSION = "' + re.escape(manifest["version"].removeprefix("v")) + r'"$', source_text, re.M))
                    break
            if not found:
                raise protocol.UpdateError("The build source archive does not contain the target application version")
        for src, target in [("backend", "app"), ("frontend/dist", "web"), ("runner", "runner"), ("tools", "tools"), ("docs", "docs"), ("LICENSE", "LICENSE"), ("layer8-source.tar.gz", "layer8-source.tar.gz")]:
            if (bundle / src).exists():
                (bundle / src).rename(self.staged / target)
        # Wheels remain under bundle/wheels until installation has completed.
        paths = [*self.staged.joinpath("web").rglob("*"), self.staged / "app/app/__init__.py", self.staged / "layer8-source.tar.gz"]
        self.hashes = {p.relative_to(self.staged).as_posix(): file_hash(p) for p in paths if p.is_file()}

    def install(self):
        run(["/usr/sbin/nginx", "-t"], timeout=30)
        run(["/usr/bin/systemctl", "reload", "nginx"], timeout=30)
        for service in SERVICES:
            run(["/usr/bin/systemctl", "is-active", "--quiet", service], timeout=15)
        self.status("backing_up", "Stopping app services and creating a verified encrypted backup")
        (STATE / "maintenance").touch(mode=0o644)
        self.stopped = True
        run(["/usr/bin/systemctl", "stop", *SERVICES], timeout=180)
        backup_dir = self.work / "backup"
        backup_dir.mkdir(mode=0o700)
        account = pwd.getpwnam("helpdesk")
        # Private work parent is traversable by the backup process, not by other accounts.
        self.work.chmod(0o750)
        os.chown(self.work, 0, account.pw_gid)
        os.chown(backup_dir, account.pw_uid, account.pw_gid)
        as_app(["-m", "app.cli", "backup", "create", "--to", str(backup_dir)], python=True)
        backups = list(backup_dir.glob("helpdesk-backup-*.hdbackup"))
        if len(backups) != 1:
            raise protocol.UpdateError("A verified pre-update backup was not created")
        self.backup = backups[0]
        as_app(["-m", "app.cli", "backup", "verify", str(self.backup)], python=True)
        self.baseline = database_report()
        self.checked("backup", "Encrypted pre-update backup created and verified")
        self.status("installing", "Backup verified; installing offline application dependencies")
        APP.rename(self.previous)
        try:
            self.staged.rename(APP)
        except BaseException:
            self.previous.rename(APP)
            raise
        self.swapped = True
        # Create at its final path: venv script shebangs must survive the switch.
        mask = os.umask(0o022)
        try:
            run(["/usr/bin/python3", "-m", "venv", str(APP / "venv")])
            run([str(APP / "venv/bin/pip"), "--isolated", "install", "--no-index", "--only-binary=:all:", "--find-links", str(APP / "bundle/wheels"), f"helpdesk=={self.request['version'].removeprefix('v')}"], env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        finally:
            os.umask(mask)
        self.status("migrating", "Applying database migrations; public access remains in maintenance")
        self.migration = True
        as_app(["upgrade", "head"])
        report = database_report()
        expected = self.request["version"].removeprefix("v")
        if report["version"] != expected or report["wheel"] != expected:
            raise protocol.UpdateError("Installed application and Python package versions do not match the release")
        self.checked("installed_version", "Installed application and Python package match the target version")
        if len(report["heads"]) != 1 or sorted(report["revisions"]) != sorted(report["heads"]):
            raise protocol.UpdateError("Database schema is not at the target migration revision")
        self.checked("schema", "Database is at the target Alembic migration revision")
        if any(report["counts"].get(name, -1) < count for name, count in self.baseline["counts"].items()):
            raise protocol.UpdateError("Core ticket data checks failed after migration")
        self.checked("core_data", "Core ticket, note, attachment and user record counts preserved")
        for path, expected_hash in self.hashes.items():
            if not (APP / path).is_file() or file_hash(APP / path) != expected_hash:
                raise protocol.UpdateError("Installed frontend or source files failed integrity checks")
        self.checked("installed_files", "Installed frontend and source files match the verified package")
        run(["/usr/bin/systemctl", "start", "helpdesk-api"], timeout=90)
        health(expected)
        self.checked("api", "API is responding with the expected release and database connection")
        if http_hash("http://127.0.0.1:8000/", 4 * 1024 * 1024) != self.hashes["web/index.html"]:
            raise protocol.UpdateError("The API is not serving the expected frontend")
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+)"', (APP / "web/index.html").read_text())
        if not assets:
            raise protocol.UpdateError("The frontend index has no application entry assets")
        for asset in assets:
            local = APP / "web" / asset.lstrip("/")
            if not local.resolve().is_relative_to((APP / "web").resolve()) or not local.is_file() or http_hash("http://127.0.0.1:8000" + asset) != file_hash(local):
                raise protocol.UpdateError("A frontend entry asset is missing or served incorrectly")
        self.checked("frontend", "Frontend index and entry assets are served correctly")
        if http_hash("http://127.0.0.1:8000/api/source") != self.hashes["layer8-source.tar.gz"]:
            raise protocol.UpdateError("The required source download does not match the installed source archive")
        self.checked("source_offer", "The exact build source archive is downloadable")
        # After the worker starts it can send mail/run integrations. Never restore a snapshot
        # over those possible external actions: a worker-start failure requires local review.
        self.committed = True
        self.status("starting", "Application healthy; starting the worker")
        worker_started = datetime.now(timezone.utc).isoformat()
        run(["/usr/bin/systemctl", "start", "helpdesk-worker"], timeout=90)
        run(["/usr/bin/systemctl", "is-active", "--quiet", *SERVICES], timeout=15)
        self.status("worker_check", "Waiting for a fresh heartbeat from the updated worker")
        worker_ready(expected, worker_started)
        self.checked("worker", "API and worker services active; fresh worker heartbeat matches the release")
        health(expected)
        self.checked("final_readiness", "Application and database readiness rechecked after worker startup")
        self.status("succeeded", "Update complete; previous application and encrypted backup retained")
        (STATE / "maintenance").unlink()

    def recover(self, error):
        if not self.stopped:
            self.status("failed", str(error))
            return
        if self.committed:
            run(["/usr/bin/systemctl", "stop", *SERVICES], timeout=180)
            self.status("recovery_required", "Application updated but worker validation failed. Services stopped; inspect locally before reopening access")
            return
        try:
            run(["/usr/bin/systemctl", "stop", *SERVICES], timeout=180)
            if self.swapped:
                APP.rename(self.staged)
                self.previous.rename(APP)
            if self.migration:
                self.status("rolling_back", "Restoring the verified pre-update backup with the previous application")
                as_app(["-m", "app.cli", "backup", "restore", str(self.backup), "--yes", "--force"], python=True)
            run(["/usr/bin/systemctl", "start", "helpdesk-api"], timeout=90)
            health(self.old_version)
            run(["/usr/bin/systemctl", "start", "helpdesk-worker"], timeout=90)
            self.status("rolled_back", f"Update failed and the previous application was restored: {error}")
            (STATE / "maintenance").unlink(missing_ok=True)
        except BaseException:
            # Neither an uncertain DB restore nor a failed restart may expose a mixed install.
            try:
                run(["/usr/bin/systemctl", "stop", *SERVICES], timeout=180)
            finally:
                self.status("recovery_required", "Automatic recovery could not finish. Keep maintenance in place and recover locally using the retained backup and previous application")


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise SystemExit("Run only through layer8-update.service, with no arguments")
    secure(STATE, directory=True)
    secure(STATE / "work", directory=True)
    secure(STATE / "progress", directory=True)
    # Inbox is intentionally group writable, but its parent and our work/status are not.
    inbox = (STATE / "inbox").lstat()
    if not stat.S_ISDIR(inbox.st_mode) or inbox.st_uid != 0 or inbox.st_mode & 0o007:
        raise SystemExit("Invalid updater inbox permissions")
    with (STATE / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (STATE / "maintenance").exists():
            raise SystemExit("An earlier update was interrupted. Recover locally before another attempt")
        updater = Updater(read_request())
        try:
            updater.prepare()
            updater.install()
        except BaseException as exc:
            message = exc if isinstance(exc, protocol.UpdateError) else protocol.UpdateError("Update step failed; inspect the updater service locally")
            updater.checks.append({"name": updater.phase, "state": "failed", "detail": str(message)})
            updater.recover(message)
        finally:
            (STATE / "inbox/request.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
