# SPDX-License-Identifier: AGPL-3.0-or-later
"""Destructive ONLY inside a disposable Docker container; never run on a host.

Real Debian install, PostgreSQL, offline wheels, encrypted backup/restore, API,
worker and Nginx. Only GitHub transport and the absent systemd manager are adapted.
The fixture signing key and all data are synthetic and remain in the container.
"""
import hashlib
import importlib.util
import io
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

if not (Path("/.dockerenv").is_file() and os.geteuid() == 0 and os.environ.get("LAYER8_DISPOSABLE_UPDATE_TEST") == "1"):
    raise SystemExit("Refusing: requires an explicitly disposable root Docker container")


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUNDLE = Path("/bundle")
command(["sh", str(BUNDLE / "install/install.sh"), "--demo", "--no-services"])
protocol = load("protocol", Path("/usr/local/lib/layer8-updater/protocol.py"))
helper = load("layer8_update", Path("/usr/local/lib/layer8-updater/layer8-update.py"))
builder = load("update_builder", Path("/src/tools/build-update.py"))
account = pwd.getpwnam("helpdesk")
environment = helper.app_env()
answers = Path("/tmp/test-update-settings.json")
answers.write_text(json.dumps({"updates": {"updates_enabled": True}, "backup": {"backup_passphrase": "synthetic appliance test passphrase"}}))
command([str(helper.APP / "venv/bin/python"), "-m", "app.cli", "settings", "import", str(answers)], cwd=helper.APP / "app", env=environment)
for path in Path("/var/lib/helpdesk/secrets").rglob("*"):
    os.chown(path, account.pw_uid, account.pw_gid)
command([str(helper.APP / "venv/bin/pip"), "install", "--no-index", "--find-links", str(BUNDLE / "wheels"), "setuptools", "wheel"])
private = Path("/tmp/test-publisher.pem")
public = protocol.KEYS / "test-publisher.pem"
command(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)])
private.chmod(0o600)
command(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)])
public.chmod(0o644)
processes = {}
service_logs = []
original_run = helper.run


def stop(name):
    process = processes.pop(name, None)
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def start(name):
    if name in processes and processes[name].poll() is None:
        return
    output = open(f"/tmp/{name}.log", "ab")
    service_logs.append(output)
    args = [str(helper.APP / "venv/bin/python"), "-m"]
    args += ["app.worker"] if name == "helpdesk-worker" else ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    processes[name] = subprocess.Popen(args, cwd=helper.APP / "app", env=environment, user=account.pw_uid, group=account.pw_gid, extra_groups=[], start_new_session=True, stdout=output, stderr=output)


def service(command_args, **kwargs):
    if command_args[0] != "/usr/bin/systemctl":
        return original_run(command_args, **kwargs)
    action = command_args[1]
    if action == "reload" and command_args[-1] == "nginx":
        return original_run(["/usr/sbin/nginx", "-s", "reload"])
    if action in ("start", "stop"):
        for name in command_args[2:]:
            (start if action == "start" else stop)(name)
        return subprocess.CompletedProcess(command_args, 0, b"", b"")
    if action == "is-active":
        if not all(name in processes and processes[name].poll() is None for name in command_args[3:]):
            raise protocol.UpdateError("Synthetic service is not active")
        return subprocess.CompletedProcess(command_args, 0, b"", b"")
    if action == "show":
        return subprocess.CompletedProcess(command_args, 0, str(processes["helpdesk-worker"].pid).encode(), b"")
    raise AssertionError(command_args)


helper.run = service
start("helpdesk-api")
start("helpdesk-worker")
helper.health("1.1.0")
command(["/usr/sbin/nginx"])


def fixture(release):
    bundle = Path("/tmp") / ("bundle-" + release)
    old_version = helper.installed_version()
    shutil.copytree(helper.APP / "app", bundle / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", ".venv"))
    (bundle / "frontend").mkdir()
    shutil.copytree(BUNDLE / "frontend/dist", bundle / "frontend/dist")
    shutil.copytree(BUNDLE / "wheels", bundle / "wheels")
    shutil.copyfile(BUNDLE / "LICENSE", bundle / "LICENSE")
    for name in ("backend/app/__init__.py", "backend/pyproject.toml"):
        path = bundle / name
        path.write_text(path.read_text().replace(json.dumps(old_version), json.dumps(release)))
    # A real new Alembic revision with an FK to an old table tests the hard
    # rollback case: pg_restore --clean by itself cannot remove this new object.
    head = helper.database_report()["heads"][0]
    revision = "updater_test_" + release.replace(".", "_")
    migration = bundle / "backend/migrations/versions" / (revision + ".py")
    migration.write_text(f'''# SPDX-License-Identifier: AGPL-3.0-or-later
from alembic import op
import sqlalchemy as sa
revision = {revision!r}
down_revision = {head!r}
branch_labels = None
depends_on = None
def upgrade():
    op.create_table({revision!r}, sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id")))
def downgrade():
    op.drop_table({revision!r})
''')
    # Rebuild a real matching wheel with the bundled build tools; no package index.
    command([str(helper.APP / "venv/bin/pip"), "wheel", "--no-index", "--find-links", str(bundle / "wheels"), "--no-deps", "--no-build-isolation", "--wheel-dir", str(bundle / "wheels"), str(bundle / "backend")])
    # setuptools leaves build/ and egg-info; release sources must exclude those.
    for directory in list((bundle / "backend").rglob("__pycache__")) + list((bundle / "backend").glob("*.egg-info")) + [bundle / "backend/build"]:
        if directory.is_dir():
            shutil.rmtree(directory)
    with tarfile.open(BUNDLE / "layer8-source.tar.gz", "r:gz") as source, tarfile.open(bundle / "layer8-source.tar.gz", "w:gz") as target:
        seen, prefix = set(), None
        for member in source:
            if not member.isfile():
                continue
            data = source.extractfile(member).read()
            for name in ("backend/app/__init__.py", "backend/pyproject.toml"):
                if member.name.endswith("/" + name):
                    data = (bundle / name).read_bytes()
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
            prefix = member.name.split("/", 1)[0]
            seen.add(member.name.split("/", 1)[1])
        for path in (bundle / "backend/migrations/versions").glob("updater_test_*.py"):
            relative = path.relative_to(bundle).as_posix()
            if relative not in seen:
                target.add(path, arcname=prefix + "/" + relative)
    output = Path("/tmp") / ("release-" + release)
    builder.build(bundle, output, release, "1.1.0", private)
    raw = (output / protocol.MANIFEST).read_bytes()
    request = {"version": "v" + release, "manifest_sha256": hashlib.sha256(raw).hexdigest(), "requested_by": 1, "id": os.urandom(16).hex()}

    def fetch(url, limit=2 * 1024 * 1024, destination=None, progress=None):
        if "/tags/" in url:
            return json.dumps({"tag_name": "v" + release, "draft": False, "prerelease": False}).encode()
        source = output / url.rsplit("/", 1)[1]
        assert source.is_file() and source.stat().st_size <= limit
        if destination:
            shutil.copyfile(source, destination)
            if progress:
                progress(source.stat().st_size, limit)
            return b""
        return source.read_bytes()

    protocol.fetch = fetch
    return helper.Updater(request)


def execute(updater):
    try:
        updater.prepare()
        updater.install()
    except Exception as exc:
        print("Expected-or-unexpected update failure:", type(exc).__name__, str(exc))
        updater.recover(exc)
    result = json.loads((helper.STATE / "status.json").read_text())
    print("Update outcome:", result["version"], result["phase"])
    return result


try:
    success = fixture("1.1.1")
    assert execute(success)["phase"] == "succeeded"
    assert not (helper.STATE / "maintenance").exists()
    assert success.backup.is_file()
    # Make build tools available in this newly installed fixture's venv too.
    command([str(helper.APP / "venv/bin/pip"), "install", "--no-index", "--find-links", str(BUNDLE / "wheels"), "setuptools", "wheel"])
    failed = fixture("1.1.2")
    original_as_app = helper.as_app

    def fail_after_migration(args, **kwargs):
        result = original_as_app(args, **kwargs)
        if args == ["upgrade", "head"]:
            raise protocol.UpdateError("Synthetic failure immediately after migration")
        return result

    helper.as_app = fail_after_migration
    assert execute(failed)["phase"] == "rolled_back"
    helper.as_app = original_as_app
    assert helper.installed_version() == "1.1.1"
    assert not (helper.STATE / "maintenance").exists()
    check = helper.as_app(["-c", "from app.db import engine; from sqlalchemy import text; c=engine.connect(); assert c.execute(text(\"SELECT to_regclass('public.updater_test_1_1_1')\")).scalar(); assert c.execute(text(\"SELECT to_regclass('public.updater_test_1_1_2')\")).scalar() is None; c.close()"], python=True)
    helper.health("1.1.1")
    uncertain = fixture("1.1.3")
    original_worker_ready = helper.worker_ready
    helper.worker_ready = lambda *args: (_ for _ in ()).throw(protocol.UpdateError("Synthetic worker validation failure"))
    assert execute(uncertain)["phase"] == "recovery_required"
    assert (helper.STATE / "maintenance").exists()
    assert not processes
    assert helper.installed_version() == "1.1.3"
    helper.worker_ready = original_worker_ready
    # An interrupted update must not be retried or clear maintenance at startup.
    sys.argv = ["layer8-update.py"]
    try:
        helper.main()
        raise AssertionError("Interrupted update was accepted")
    except SystemExit as exc:
        assert "interrupted" in str(exc)
    assert (helper.STATE / "maintenance").exists()
    print("Real PostgreSQL encrypted backup/restore, successful upgrade, failed migration recovery, worker failure isolation and interruption guard passed")
finally:
    for name in list(processes):
        stop(name)
    subprocess.run(["/usr/sbin/nginx", "-s", "stop"], check=False)
    for output in service_logs:
        output.close()
