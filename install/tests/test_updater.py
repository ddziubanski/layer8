# SPDX-License-Identifier: AGPL-3.0-or-later
"""Root updater tests use temporary trees and replace every OS/service operation."""
import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


protocol = module("protocol", ROOT / "backend/app/update_protocol.py")
helper = module("layer8_update", ROOT / "install/files/layer8-update.py") if os.name == "posix" else None


@unittest.skipUnless(os.name == "posix", "appliance updater is POSIX-only")
class ArchiveTests(unittest.TestCase):
    def test_reject_unsafe_members_before_writing_outside_destination(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("/etc/shadow", tarfile.REGTYPE), ("backend/link", tarfile.SYMTYPE), ("backend/hard", tarfile.LNKTYPE), ("backend/fifo", tarfile.FIFOTYPE), ("backend\\escape", tarfile.REGTYPE), ("install/run.sh", tarfile.REGTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "package.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.linkname = kind, "../../escape"
                    output.addfile(entry)
                destination = root / "destination"
                destination.mkdir()
                with self.assertRaises(protocol.UpdateError):
                    helper.unpack(archive, destination)
                self.assertFalse((root / "escape").exists())

    def test_duplicate_and_size_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for duplicate in (True, False):
                archive = root / "package.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    entry = tarfile.TarInfo("backend/large")
                    output.addfile(entry)
                    if duplicate:
                        output.addfile(entry)
                target = root / str(duplicate)
                target.mkdir()
                with patch.object(protocol, "MAX_EXPANDED", -1 if not duplicate else protocol.MAX_EXPANDED):
                    with self.assertRaises(protocol.UpdateError):
                        helper.unpack(archive, target)

    def test_root_files_cannot_be_group_writable_or_symlinks(self):
        for mode in (stat.S_IFREG | 0o664, stat.S_IFLNK | 0o777):
            path = Mock()
            path.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=mode)
            with self.assertRaises(protocol.UpdateError):
                helper.secure(path)


@unittest.skipUnless(os.name == "posix", "appliance updater is POSIX-only")
class UpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "progress").mkdir()
        self.app = self.root / "helpdesk"
        self.app.mkdir()
        (self.app / "old").write_text("old")
        self.calls = []
        self.failure = None
        for name, value in [("APP", self.app), ("STATE", self.state), ("run", self.run_command), ("as_app", self.app_command), ("health", self.health), ("worker_ready", self.worker), ("database_report", self.report), ("http_hash", self.http_hash)]:
            patcher = patch.object(helper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(helper.os, "chown")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(helper.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.updater = helper.Updater({"version": "v1.2.0", "id": "a" * 32, "manifest_sha256": "b" * 64, "requested_by": 1})
        self.updater.work.mkdir(parents=True)
        self.updater.previous = self.root / "previous"
        self.updater.staged = self.root / "staged"
        self.updater.staged.mkdir()
        self.updater.old_version = "1.1.0"
        for path, body in {"web/index.html": '<script src="/assets/app.js"></script>', "web/assets/app.js": "app", "layer8-source.tar.gz": "source", "app/app/__init__.py": 'VERSION = "1.2.0"'}.items():
            target = self.updater.staged / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        self.updater.hashes = {p.relative_to(self.updater.staged).as_posix(): helper.file_hash(p) for p in self.updater.staged.rglob("*") if p.is_file()}
        self.reports = 0

    def run_command(self, command, **kwargs):
        self.calls.append(tuple(command))
        if self.failure == "pip" and "--no-index" in command:
            raise protocol.UpdateError("Offline dependency install failed")
        return SimpleNamespace(stdout=b"", returncode=0)

    def app_command(self, arguments, **kwargs):
        self.calls.append(tuple(arguments))
        if "create" in arguments and "backup" in arguments:
            (Path(arguments[-1]) / "helpdesk-backup-test.hdbackup").write_bytes(b"test")
        if self.failure == "migration" and arguments == ["upgrade", "head"]:
            raise protocol.UpdateError("Migration failed")
        if self.failure == "restore" and "restore" in arguments:
            raise protocol.UpdateError("Recovery failed")
        return SimpleNamespace(stdout=b"{}", returncode=0)

    def health(self, expected):
        self.calls.append(("health", expected))
        if self.failure in ("health", "restore") and expected == "1.2.0":
            raise protocol.UpdateError("Health check failed")

    def worker(self, expected, since):
        self.calls.append(("worker_ready", expected))
        if self.failure == "worker":
            raise protocol.UpdateError("Worker heartbeat failed")

    def report(self):
        self.reports += 1
        if self.failure == "files" and self.reports > 1:
            (self.app / "web/assets/app.js").write_text("damaged")
        return {"version": "1.1.0" if self.reports == 1 else "1.2.0", "wheel": "wrong" if self.failure == "version" else "1.2.0", "heads": ["new"], "revisions": ["wrong" if self.failure == "schema" and self.reports > 1 else "new"], "counts": {"tickets": 10 if self.failure != "data" or self.reports == 1 else 9}}

    def http_hash(self, url, maximum=None):
        path = "web/index.html" if url.endswith(":8000/") else "layer8-source.tar.gz" if url.endswith("/api/source") else "web/" + url.split(":8000/")[1]
        if self.failure == "frontend" and path == "web/index.html":
            return "bad"
        if self.failure == "source" and path == "layer8-source.tar.gz":
            return "bad"
        return helper.file_hash(self.app / path)

    def execute(self):
        try:
            self.updater.install()
        except protocol.UpdateError as exc:
            self.updater.recover(exc)
        return json.loads((self.state / "status.json").read_text())

    def test_success_requires_schema_data_files_api_frontend_source_and_worker_checks(self):
        result = self.execute()
        self.assertEqual(result["phase"], "succeeded")
        self.assertFalse((self.state / "maintenance").exists())
        self.assertTrue((self.updater.previous / "old").is_file())
        names = {item["name"] for item in result["checks"]}
        self.assertTrue({"backup", "installed_version", "schema", "core_data", "installed_files", "api", "frontend", "source_offer", "worker"} <= names)
        public = json.loads((self.state / "progress" / ("a" * 32 + ".json")).read_text())
        for forbidden in ("backup", "previous", "requested_by", "request_id"):
            self.assertNotIn(forbidden, public)
        self.assertNotIn(str(self.root), json.dumps(public))
        self.assertEqual(public["step"], public["steps"])

    def test_pre_commit_failures_restore_old_code_and_only_restore_db_after_migration(self):
        for failure in ("pip", "migration", "schema", "data", "health", "frontend", "source", "version", "files"):
            with self.subTest(failure=failure):
                # Each scenario needs a separate application/backup tree.
                case = UpdateFlowTests(methodName="test_success_requires_schema_data_files_api_frontend_source_and_worker_checks")
                case.setUp()
                try:
                    case.failure = failure
                    result = case.execute()
                    self.assertEqual(result["phase"], "rolled_back")
                    self.assertTrue((case.app / "old").is_file())
                    self.assertFalse((case.state / "maintenance").exists())
                    restored = any("restore" in command for command in case.calls)
                    self.assertEqual(restored, failure != "pip")
                finally:
                    case.doCleanups()

    def test_uncertain_restore_or_worker_failure_keeps_maintenance_and_services_stopped(self):
        for failure in ("restore", "worker"):
            case = UpdateFlowTests(methodName="test_success_requires_schema_data_files_api_frontend_source_and_worker_checks")
            case.setUp()
            try:
                case.failure = failure
                result = case.execute()
                self.assertEqual(result["phase"], "recovery_required")
                self.assertTrue((case.state / "maintenance").exists())
                self.assertEqual(case.calls[-1], ("/usr/bin/systemctl", "stop", "helpdesk-api", "helpdesk-worker"))
                if failure == "worker":
                    self.assertFalse(any("restore" in command for command in case.calls))
            finally:
                case.doCleanups()
