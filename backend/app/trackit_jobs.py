# SPDX-License-Identifier: AGPL-3.0-or-later
"""Track-It! imports from Settings > Imports: the same importer as `python -m app.cli import-trackit`.

A bundle can be hundreds of MB and a dry run of a long history can take minutes, so:
- the upload is staged once, next to the storage folder (not in it, so backups never carry it),
  mode 0600, and the page sends only its id afterwards;
- dry runs and imports run in a background thread; their state is a small JSON file beside the
  bundle, so every API process can answer the page's polls;
- an import must follow a finished dry run with the same choices (as KACE's preview -> apply);
- the bundle holds a site's real data: it's deleted after a successful import, on cancel, and
  after STALE_HOURS.
"""
import json
import logging
import os
import secrets
import shutil
import threading
import time
from pathlib import Path

from app import trackit
from app.config import settings

log = logging.getLogger(__name__)

MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # nginx allows 1 GiB; this is the API's own ceiling
STALE_HOURS = 24
_lock = threading.Lock()


def root() -> Path:
    path = settings.storage_dir.resolve().parent / "import-staging" / "trackit"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _dir(job_id: str) -> Path:
    if not (len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)):
        raise KeyError(job_id)
    path = root() / job_id
    if not path.is_dir():
        raise KeyError(job_id)
    return path


def bundle_path(job_id: str) -> Path:
    return _dir(job_id) / "bundle.zip"


def state(job_id: str) -> dict:
    path = _dir(job_id) / "state.json"
    data = json.loads(path.read_text())
    # A run whose process is gone (API restarted mid-run) is reported as interrupted.
    if data.get("state") == "running" and not _alive(data.get("pid")):
        data.update(state="failed", error="The run was interrupted (the service restarted). Run it again.")
        _write(job_id, data)
    return data


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _write(job_id: str, data: dict) -> None:
    path = _dir(job_id) / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    tmp.replace(path)


def cleanup() -> None:
    """Delete staged bundles older than STALE_HOURS (unless running)."""
    cutoff = time.time() - STALE_HOURS * 3600
    for path in root().iterdir():
        try:
            data = json.loads((path / "state.json").read_text())
        except (OSError, ValueError):
            data = {}
        if path.stat().st_mtime < cutoff and not (data.get("state") == "running" and _alive(data.get("pid"))):
            shutil.rmtree(path, ignore_errors=True)


def stage(chunks) -> str:
    """Write an uploaded bundle; check it's a Track-It! bundle. Returns the job id."""
    cleanup()
    job_id = secrets.token_hex(16)
    path = root() / job_id
    path.mkdir(mode=0o700)
    target = path / "bundle.zip"
    size = 0
    try:
        with open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise trackit.ImportError_("Bundles are limited to 2 GiB here; export in parts with -Since, or use the command line")
                f.write(chunk)
        bundle = trackit.Bundle(str(target))
        try:
            m = bundle.manifest
            info = {
                "server": m.get("server"), "database": m.get("database"), "exported_at": m.get("exported_at"), "since": m.get("since"),
                "size": size, "fields": [{**f, "top": [[str(v), n] for v, n in f["top"]]} for f in trackit.detect(bundle)],
            }
        finally:
            bundle.zip.close()
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    (path / "info.json").write_text(json.dumps(info))
    _write(job_id, {"state": "staged"})
    return job_id


def info(job_id: str) -> dict:
    return json.loads((_dir(job_id) / "info.json").read_text())


def report_dict(report: trackit.Report) -> dict:
    return {"counts": dict(report.counts), "lines": report.lines(), "problems": report.counts.get("problems", 0)}


def start(job_id: str, choices: dict, apply: bool, actor_id: int, factory) -> dict:
    """Begin a dry run, or (after a dry run with the same choices) the import."""
    with _lock:
        for other in root().iterdir():
            if other.name != job_id and (other / "state.json").exists():
                try:
                    if state(other.name).get("state") == "running":
                        raise trackit.ImportError_("Another Track-It! import is running. Wait for it to finish.")
                except KeyError:
                    pass
        current = state(job_id)
        if current.get("state") == "running":
            raise trackit.ImportError_("This bundle is already running.")
        if apply and not (current.get("state") == "done" and not current.get("apply") and current.get("used") == choices):
            raise trackit.ImportError_("Run a dry run with these choices first, then import.")
        if current.get("state") == "done" and current.get("apply"):
            raise trackit.ImportError_("This bundle is already imported.")
        data = {"state": "running", "apply": apply, "used": choices, "pid": os.getpid(), "started_at": time.time(),
                "dry_run": current.get("report") if apply else None}
        _write(job_id, data)
    launch(job_id, choices, apply, actor_id, factory)
    return data


def launch(job_id: str, choices: dict, apply: bool, actor_id: int, factory) -> None:
    """Run in a background thread (tests replace this to run inline)."""
    threading.Thread(target=work, args=(job_id, choices, apply, actor_id, factory), name=f"trackit-{job_id[:8]}", daemon=True).start()


def work(job_id: str, choices: dict, apply: bool, actor_id: int, factory) -> None:
    data = state(job_id)
    try:
        with factory() as session:
            report = trackit.run(session, str(bundle_path(job_id)), apply=apply, queue_name=choices.get("queue"),
                                 queue_map=choices.get("groups") or {}, labels=choices.get("labels") or {},
                                 skip=set(choices.get("skip") or []), departments=choices.get("departments") or "match")
        data.update(state="done", report=report_dict(report), finished_at=time.time())
        if apply:
            log.info("Track-It! import applied by user ID %s: %s new tickets, %s updated", actor_id,
                     report.counts.get("ticket_new", 0), report.counts.get("ticket_updated", 0))
    except trackit.ImportError_ as exc:
        data.update(state="failed", error=str(exc))
    except Exception as exc:  # shown to the admin; details in the log
        log.exception("Track-It! %s failed", "import" if apply else "dry run")
        data.update(state="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
    _write(job_id, data)
    if apply and data["state"] == "done":
        # The real data has done its job: keep only the report.
        bundle_path(job_id).unlink(missing_ok=True)


def discard(job_id: str) -> None:
    path = _dir(job_id)
    if state(job_id).get("state") == "running":
        raise trackit.ImportError_("Wait for the run to finish before discarding it.")
    shutil.rmtree(path, ignore_errors=True)
