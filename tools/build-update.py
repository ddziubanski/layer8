#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release publisher: package an existing offline ISO bundle and sign its manifest.

The private Ed25519 key is supplied out-of-band; never generate or commit it here.
Publish the three resulting files on the matching GitHub release after VM testing.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "app"))
from update_protocol import MANIFEST, SIGNATURE, MAX_PACKAGE, REPOSITORY, version


def build(bundle: Path, out: Path, release: str, minimum: str, key: Path):
    version(release)
    version(minimum)
    release = release.removeprefix("v")
    minimum = minimum.removeprefix("v")
    if version(minimum) >= version(release):
        raise ValueError("The minimum version must precede the new version")
    for name in ("backend/app/__init__.py", "backend/alembic.ini", "frontend/dist/index.html", "layer8-source.tar.gz", "LICENSE"):
        if not (bundle / name).is_file():
            raise ValueError(f"Offline bundle is missing {name}")
    if not re.search(r'^VERSION = "' + re.escape(release) + r'"$', (bundle / "backend/app/__init__.py").read_text(), re.M):
        raise ValueError("Bundle application VERSION must match the release")
    if not list((bundle / "wheels").glob(f"helpdesk-{release}-*.whl")):
        raise ValueError("Bundle must contain the matching helpdesk wheel and all offline dependencies")
    # Bundle VERSION may contain a build stamp; only the application version is used here.
    out.mkdir(parents=True, exist_ok=True)
    package = out / f"layer8-v{release}-update.tar.gz"
    import io
    with tarfile.open(package, "x:gz") as archive:
        body = (release + "\n").encode()
        info = tarfile.TarInfo("VERSION")
        info.size, info.mode = len(body), 0o644
        archive.addfile(info, io.BytesIO(body))
        for name in ("backend", "frontend", "wheels", "runner", "tools", "docs", "LICENSE", "layer8-source.tar.gz"):
            root = bundle / name
            if not root.exists():
                continue
            for path in ([root] if root.is_file() else [root, *sorted(root.rglob("*"))]):
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise ValueError("Update bundles must contain only regular files and directories")
                if path.suffix in {".hdbackup", ".hdexport"} or path.name in {".env", "secrets", "storage", "node_modules", ".git", ".venv", "__pycache__"}:
                    raise ValueError(f"Remove non-release material from the bundle: {path.name}")
                info = archive.gettarinfo(str(path), path.relative_to(bundle).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as source:
                        archive.addfile(info, source)
                else:
                    archive.addfile(info)
    size = package.stat().st_size
    if size > MAX_PACKAGE:
        raise ValueError("Update package exceeds the 1 GiB limit")
    with package.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = {"format": 1, "repository": REPOSITORY, "version": release, "minimum_version": minimum, "platform": "debian13-amd64", "python": "3.13", "package": package.name, "size": size, "sha256": digest}
    (out / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(out / MANIFEST), "-out", str(out / SIGNATURE)], check=True)
    if (out / SIGNATURE).stat().st_size != 64:
        raise ValueError("Signing key must be Ed25519")
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-version", required=True)
    parser.add_argument("--key", required=True, type=Path)
    args = parser.parse_args()
    print(build(args.bundle, args.out, args.version, args.minimum_version, args.key))
