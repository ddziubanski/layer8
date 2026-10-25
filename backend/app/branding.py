# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings > Branding: product name, texts, theme, and the background, logo and favicon images.

Images are public (the sign-in page shows them before anyone signs in), kept in
storage_dir/branding, and only accepted when their content is really JPEG, GIF or PNG (or ICO,
for the favicon). SVG isn't, as it can carry script.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.configstore import write_secret_file
from app.models import AppSetting, User

IMAGES = {"background": "Background", "logo": "Logo", "favicon": "Favicon"}
MAX_BYTES = 8 * 1024 * 1024
SETTING_KEY = "branding_files"


class BrandingError(ValueError):
    """The message is safe to show."""


def sniff(data: bytes) -> str | None:
    """The image's real type from its first bytes, or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return None


def _path(name: str):
    return settings.storage_dir / "branding" / name


def files(session: Session) -> dict:
    row = session.get(AppSetting, SETTING_KEY)
    return dict(row.value) if row else {}


def _save_meta(session: Session, meta: dict, by: User | None) -> None:
    row = session.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY, value={})
        session.add(row)
    row.value = meta
    row.updated_at = datetime.now().astimezone()
    row.updated_by_id = by.id if by else None


def store(session: Session, name: str, data: bytes, by: User | None) -> dict:
    if name not in IMAGES:
        raise BrandingError("Unknown image")
    if len(data) > MAX_BYTES:
        raise BrandingError(f"Images are limited to {MAX_BYTES // (1024 * 1024)} MB")
    content_type = sniff(data)
    if content_type == "image/x-icon" and name != "favicon":
        content_type = None
    if content_type is None:
        raise BrandingError("Upload a JPG, GIF or PNG image" + (" (or an ICO file)" if name == "favicon" else ""))
    # Not secret, but the same atomic write.
    write_secret_file(_path(name), data)
    meta = files(session)
    version = int(meta.get(name, {}).get("version", 0)) + 1
    meta[name] = {"content_type": content_type, "size": len(data), "version": version, "uploaded_at": datetime.now().astimezone().isoformat()}
    _save_meta(session, meta, by)
    return meta[name]


def remove(session: Session, name: str, by: User | None) -> None:
    _path(name).unlink(missing_ok=True)
    meta = files(session)
    meta.pop(name, None)
    _save_meta(session, meta, by)


def url(session: Session, name: str) -> str | None:
    meta = files(session).get(name)
    if not meta or not _path(name).is_file():
        return None
    # The version changes on each upload, so browsers can cache each one for long.
    return f"/api/branding/files/{name}?v={meta['version']}"


def public(session: Session) -> dict:
    return {
        "product_name": settings.product_name,
        "sign_in_message": settings.sign_in_message,
        "sign_in_heading": settings.sign_in_heading,
        "sign_in_layout": settings.sign_in_layout,
        "sign_in_card_size": settings.sign_in_card_size,
        "sign_in_background_fit": settings.sign_in_background_fit,
        "sign_in_background_position": settings.sign_in_background_position,
        "sign_in_background_overlay": settings.sign_in_background_overlay,
        "sign_in_backdrop": settings.sign_in_backdrop,
        "background_in_console": settings.background_in_console,
        "portal_welcome": settings.portal_welcome,
        "theme_preset": settings.theme_preset,
        "theme_brand": settings.theme_brand,
        "theme_sidebar": settings.theme_sidebar,
        "dark_mode": settings.dark_mode,
        "background_url": url(session, "background"),
        "logo_url": url(session, "logo"),
        "favicon_url": url(session, "favicon"),
    }


def file_for(session: Session, name: str):
    """(path, content_type) of an uploaded image, or None."""
    meta = files(session).get(name)
    if name not in IMAGES or not meta or not _path(name).is_file():
        return None
    return _path(name), meta["content_type"]
