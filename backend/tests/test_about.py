# SPDX-License-Identifier: AGPL-3.0-or-later
import tomllib
from pathlib import Path

from app import LICENSE, VERSION
from app.config import settings


def test_about_names_version_licence_and_source_without_signing_in(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "source_archive", tmp_path / "missing.tar.gz")
    about = client.get("/api/about").json()
    assert about == {"product": "Layer 8", "version": VERSION, "license": "AGPL-3.0-or-later",
                     "source_download": None, "source_repository": settings.source_repository}
    assert client.get("/api/source").status_code == 404
    archive = tmp_path / "layer8-source.tar.gz"
    archive.write_bytes(b"\x1f\x8bsource")
    monkeypatch.setattr(settings, "source_archive", archive)
    assert client.get("/api/about").json()["source_download"] == "/api/source"
    r = client.get("/api/source")
    assert r.status_code == 200 and r.content == b"\x1f\x8bsource"
    assert f"layer8-{VERSION}-source.tar.gz" in r.headers["content-disposition"]


def test_versions_and_licence_agree_everywhere():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "backend/pyproject.toml").read_text())["project"]
    assert project["version"] == VERSION and project["license"] == LICENSE == "AGPL-3.0-or-later"
    import json
    package = json.loads((root / "frontend/package.json").read_text())
    assert package["version"] == VERSION and package["license"] == LICENSE
    assert (root / "LICENSE").read_text().lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
