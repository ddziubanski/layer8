# SPDX-License-Identifier: AGPL-3.0-or-later
"""Track-It! import from Settings > Imports: stage, dry run, import, discard. Made-up data only."""

import pytest
from sqlalchemy import select

from app import trackit_jobs
from app.models import Ticket
from tests.conftest import as_user
from tests.test_trackit_import import TABLES, bundle, desk  # noqa: F401 (fixture)


@pytest.fixture
def inline(monkeypatch):
    """Run jobs in the request, not a thread, so a test sees the result."""
    monkeypatch.setattr(trackit_jobs, "launch", trackit_jobs.work)


def upload(client, user, data):
    return client.post("/api/imports/trackit", headers=as_user(user), files={"file": ("trackit.zip", data, "application/zip")})


def made(tmp_path):
    return open(bundle(tmp_path / "b.zip", TABLES, {77: b"\x89PNG fake"}), "rb").read()


def test_stage_dry_run_import_and_cleanup(client, db, desk, admin, inline, tmp_path):  # noqa: F811
    staged = upload(client, admin, made(tmp_path))
    assert staged.status_code == 200, staged.text
    job = staged.json()
    assert job["state"] == "staged" and {f["column"] for f in job["fields"]} >= {"OPENED_GROUP", "HD_LOOKUP01"}
    folder = trackit_jobs.root() / job["id"]
    assert (folder / "bundle.zip").stat().st_mode & 0o777 == 0o600
    # Staged next to the storage folder, never inside it (backups copy that folder).
    assert not folder.resolve().is_relative_to(trackit_jobs.settings.storage_dir.resolve())

    run = lambda **body: client.post(f"/api/imports/trackit/{job['id']}/run", headers=as_user(admin), json=body)  # noqa: E731
    choices = {"groups": {"Desktop": "Desktop support"}, "labels": {"HD_LOOKUP01": "Building"}}
    # No import without a dry run with the same choices.
    assert run(**choices, apply=True, confirm=True).status_code == 409
    dry = run(**choices)
    assert dry.status_code == 200, dry.text
    assert dry.json()["state"] == "done" and not dry.json()["apply"]
    assert dry.json()["report"]["counts"]["ticket_new"] == 3
    assert db.scalar(select(Ticket).where(Ticket.subject == "Printer jammed")) is None
    # Other choices need their own dry run; and an import needs confirm.
    assert run(groups={}, apply=True, confirm=True).status_code == 409
    assert run(**choices, apply=True).status_code == 409

    done = run(**choices, apply=True, confirm=True)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["state"] == "done" and body["apply"] and body["dry_run"]["counts"]["ticket_new"] == 3
    db.expire_all()
    assert db.scalar(select(Ticket).where(Ticket.subject == "Printer jammed")) is not None
    # The real data is gone once imported; only the report stays.
    assert not (folder / "bundle.zip").exists()
    assert run(**choices).status_code == 409

    assert client.delete(f"/api/imports/trackit/{job['id']}", headers=as_user(admin)).status_code == 204
    assert client.get(f"/api/imports/trackit/{job['id']}", headers=as_user(admin)).status_code == 404


def test_admins_only_and_bad_input(client, db, desk, admin, agent, inline, tmp_path):  # noqa: F811
    assert upload(client, agent, made(tmp_path)).status_code == 403
    bad = upload(client, admin, b"not a zip")
    assert bad.status_code == 422 and "Not a Track-It! export bundle" in bad.json()["detail"]
    assert list(trackit_jobs.root().iterdir()) == []  # nothing left behind
    job = upload(client, admin, made(tmp_path)).json()
    nope = client.post(f"/api/imports/trackit/{job['id']}/run", headers=as_user(admin), json={"queue": "No such queue"})
    assert nope.status_code == 422
    assert client.get("/api/imports/trackit/../../etc", headers=as_user(admin)).status_code == 404
    assert client.get("/api/imports/trackit/" + "0" * 32, headers=as_user(admin)).status_code == 404


def test_interrupted_run_and_stale_bundles(client, db, desk, admin, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(trackit_jobs, "launch", lambda *a: None)  # "started", then the process died
    job = upload(client, admin, made(tmp_path)).json()
    assert client.post(f"/api/imports/trackit/{job['id']}/run", headers=as_user(admin), json={}).json()["state"] == "running"
    state = trackit_jobs.state(job["id"])
    trackit_jobs._write(job["id"], {**state, "pid": 999999999})
    again = client.get(f"/api/imports/trackit/{job['id']}", headers=as_user(admin)).json()
    assert again["state"] == "failed" and "interrupted" in again["error"]

    folder = trackit_jobs.root() / job["id"]
    import os, time
    old = time.time() - (trackit_jobs.STALE_HOURS + 1) * 3600
    os.utime(folder, (old, old))
    trackit_jobs.cleanup()
    assert not folder.exists()


def test_failed_run_reports_error(client, db, desk, admin, inline, tmp_path):  # noqa: F811
    job = upload(client, admin, made(tmp_path)).json()
    r = client.post(f"/api/imports/trackit/{job['id']}/run", headers=as_user(admin), json={"skip": ["NOT_A_FIELD"]}).json()
    assert r["state"] == "failed" and "Unknown Track-It! field" in r["error"]


def test_dry_run_in_a_real_background_thread(client, db, desk, admin, tmp_path):  # noqa: F811
    import time

    job = upload(client, admin, made(tmp_path)).json()
    started = client.post(f"/api/imports/trackit/{job['id']}/run", headers=as_user(admin), json={})
    assert started.status_code == 200, started.text
    for _ in range(100):
        state = client.get(f"/api/imports/trackit/{job['id']}", headers=as_user(admin)).json()
        if state["state"] != "running":
            break
        time.sleep(0.1)
    assert state["state"] == "done", state
    assert state["report"]["counts"]["ticket_new"] == 3
