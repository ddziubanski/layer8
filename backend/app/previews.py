"""Which attachments the browser may show inline, and as what.

Only formats that can't run script in our origin are previewed: raster images,
PDF and plain text. HTML, SVG, XML and everything else is download-only.
"""

import mimetypes

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
TEXT_EXTENSIONS = {".txt", ".log", ".csv", ".json", ".ini", ".cfg", ".conf", ".md", ".ps1", ".sh", ".py", ".yaml", ".yml"}

# What we send for each kind. The stored type is what the uploader claimed, so it's
# never echoed back for previews.
SERVE_AS = {"pdf": "application/pdf", "text": "text/plain; charset=utf-8"}


def preview_kind(content_type: str, filename: str) -> str | None:
    ctype = (content_type or "").split(";")[0].strip().lower()
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ctype in IMAGE_TYPES:
        return "image"
    if ctype == "application/pdf" or ext == ".pdf":
        return "pdf"
    if ctype == "text/plain" or ctype == "text/csv" or ext in TEXT_EXTENSIONS:
        return "text"
    return None


def serve_type(kind: str, content_type: str, filename: str) -> str:
    if kind == "image":
        ctype = (content_type or "").split(";")[0].strip().lower()
        return ctype if ctype in IMAGE_TYPES else (mimetypes.guess_type(filename)[0] or "application/octet-stream")
    return SERVE_AS[kind]
