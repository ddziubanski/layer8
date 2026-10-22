"""Local-disk file store for attachments and raw emails.

Files are stored by an opaque key, never by their original name, so uploaded
filenames can't cause path tricks. Swap this module for object storage later.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class StoredFile:
    key: str
    sha256: str
    size: int


def _path(key: str) -> Path:
    # Keys are made here, but one could come from an imported export: never outside the folder.
    root = settings.storage_dir.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Storage key outside the storage folder: {key!r}")
    return path


def save(data: bytes) -> StoredFile:
    now = datetime.now(UTC)
    key = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}"
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return StoredFile(key=key, sha256=hashlib.sha256(data).hexdigest(), size=len(data))


def path(key: str) -> Path:
    return _path(key)


def read(key: str) -> bytes:
    return _path(key).read_bytes()


def delete(key: str) -> None:
    _path(key).unlink(missing_ok=True)
