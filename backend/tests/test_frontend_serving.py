# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from app.config import settings


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "app-123.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    monkeypatch.setattr(settings, "frontend_dir", tmp_path)
    return tmp_path


def test_index_assets_and_client_routes(client, dist):
    root = client.get("/")
    assert root.text == "<html>app</html>" and root.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in root.headers["content-security-policy"]
    asset = client.get("/assets/app-123.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    # A reload on a client-side route still gets the app.
    assert client.get("/tickets/1000").text == "<html>app</html>"
    # A script from an older build is gone, not the app (which the browser would refuse as a script).
    assert client.get("/assets/app-old.js").status_code == 404


def test_api_paths_and_traversal_are_not_served(client, dist):
    assert client.get("/api/nope").status_code == 404
    assert "nope" not in client.get("/../secret.txt").text
    assert "nope" not in client.get("/%2e%2e/secret.txt").text


def test_no_build_means_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "frontend_dir", tmp_path / "missing")
    assert client.get("/").status_code == 404
