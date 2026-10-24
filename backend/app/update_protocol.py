# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded, fixed-origin release protocol, also installed beside the root updater.

Standard library only. A GitHub checksum is not a publisher signature: the manifest
must verify against an independently provisioned, root-owned Ed25519 public key.
"""
import base64
import hashlib
import json
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "ddziubanski/layer8"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
MANIFEST = "layer8-update.json"
SIGNATURE = "layer8-update.sig"
MAX_PACKAGE = 1024 * 1024 * 1024
MAX_EXPANDED = 4 * MAX_PACKAGE
KEYS = Path("/etc/helpdesk/update-keys")


class UpdateError(ValueError):
    """Safe, bounded text for the admin UI; never include process output or secrets."""


def version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})", value):
        raise UpdateError("Updates require a stable version such as v1.2.0")
    return tuple(map(int, value.removeprefix("v").split(".")))


def tag(value: str) -> str:
    version(value)
    return "v" + value.removeprefix("v")


def asset_url(release: str, name: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/{tag(release)}/{name}"


def allowed_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.hostname in {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
            and not parsed.username and not parsed.password and port in (None, 443))


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise UpdateError("GitHub returned an unexpected download location")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int = 2 * 1024 * 1024, destination: Path | None = None, progress=None) -> bytes:
    if not allowed_url(url):
        raise UpdateError("Unexpected update source")
    # No cookies, tokens, ambient proxy credentials or user-supplied hosts.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Layer8-release-updater", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener.open(request, timeout=30) as response:
            data = bytearray()
            output = destination.open("xb") if destination else None
            count = 0
            try:
                while chunk := response.read(min(1024 * 1024, limit + 1 - count)):
                    count += len(chunk)
                    if count > limit:
                        raise UpdateError("GitHub update response exceeds the size limit")
                    if output:
                        output.write(chunk)
                        if progress:
                            progress(count, limit)
                    else:
                        data.extend(chunk)
            finally:
                if output:
                    output.close()
            return bytes(data)
    except urllib.error.HTTPError as exc:
        raise UpdateError("GitHub rate limit or access denied; try later" if exc.code in (403, 429) else "GitHub release or update asset is unavailable") from None
    except (OSError, urllib.error.URLError):
        raise UpdateError("Could not reach GitHub securely; check the server's internet connection") from None


def releases() -> list[dict]:
    try:
        rows = json.loads(fetch(API + "?per_page=100"))
        if not isinstance(rows, list):
            raise ValueError
        return [row for row in rows if isinstance(row, dict) and not row.get("draft") and not row.get("prerelease")]
    except (ValueError, TypeError):
        raise UpdateError("GitHub returned invalid release metadata") from None


def latest() -> dict | None:
    stable = []
    for row in releases():
        try:
            stable.append((version(row.get("tag_name")), row))
        except UpdateError:
            continue
    return max(stable, key=lambda item: item[0])[1] if stable else None


def verify_manifest(raw: bytes, signature: bytes, release: str, keys: Path = KEYS) -> dict:
    if len(raw) > 65536 or len(signature) != 64:
        raise UpdateError("Invalid update manifest or signature")
    trusted = sorted(keys.glob("*.pem")) if keys.is_dir() else []
    if not trusted:
        raise UpdateError("No trusted update signing key is installed on this server")
    verified = False
    with tempfile.TemporaryDirectory(prefix="layer8-signature-") as temporary:
        root = Path(temporary)
        (root / "manifest").write_bytes(raw)
        (root / "signature").write_bytes(signature)
        for key in trusted[:20]:
            if key.is_symlink() or not key.is_file():
                continue
            try:
                pem = key.read_text().strip().splitlines()
                der = base64.b64decode("".join(pem[1:-1]), validate=True)
                if pem[0] != "-----BEGIN PUBLIC KEY-----" or pem[-1] != "-----END PUBLIC KEY-----" or len(der) != 44 or not der.startswith(bytes.fromhex("302a300506032b6570032100")):
                    continue
            except (OSError, ValueError, IndexError):
                continue
            try:
                result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key), "-rawin", "-in", str(root / "manifest"), "-sigfile", str(root / "signature")], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                raise UpdateError("The update signature verifier is unavailable") from None
            if result.returncode == 0:
                verified = True
                break
    if not verified:
        raise UpdateError("Update signature does not match a trusted signing key")
    try:
        manifest = json.loads(raw)
        expected = {"format", "repository", "version", "minimum_version", "platform", "python", "package", "sha256", "size"}
        if not isinstance(manifest, dict) or set(manifest) != expected:
            raise ValueError
        if manifest["format"] != 1 or manifest["repository"] != REPOSITORY or tag(manifest["version"]) != tag(release):
            raise ValueError
        version(manifest["minimum_version"])
        if manifest["platform"] != "debian13-amd64" or manifest["python"] != "3.13":
            raise UpdateError("This update requires the Debian 13 amd64 appliance with Python 3.13")
        if manifest["package"] != f"layer8-{tag(release)}-update.tar.gz" or not re.fullmatch("[0-9a-f]{64}", manifest["sha256"]):
            raise ValueError
        if type(manifest["size"]) is not int or not 0 < manifest["size"] <= MAX_PACKAGE:
            raise ValueError
        return manifest
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Invalid signed update manifest") from None


def signed_release(release: str, keys: Path = KEYS) -> tuple[dict, str]:
    raw = fetch(asset_url(release, MANIFEST), 65536)
    signature = fetch(asset_url(release, SIGNATURE), 64)
    return verify_manifest(raw, signature, release, keys), hashlib.sha256(raw).hexdigest()


def compatible(manifest: dict, installed: str) -> None:
    if version(manifest["version"]) <= version(installed):
        raise UpdateError("Only a newer stable release can be installed; downgrades are refused")
    if version(installed) < version(manifest["minimum_version"]):
        raise UpdateError(f"First update to at least {manifest['minimum_version']}")
