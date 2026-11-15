# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request size limits, enforced before anything is read or parsed.

FastAPI reads and parses a request body before it runs a route's sign-in check, so without this
anyone could make the API hold a very large body in memory just by sending it. Every request is
held to a small limit unless its path is an upload, and an upload's body isn't read at all until
the caller is known to be signed in (the public receiving photo excepted: it has its own code
check and a small limit). Each upload route still enforces its own exact limit as it reads.
"""

import json
import re
from collections.abc import Callable

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app import kace, trackit_jobs
from app.config import settings

MB = 1024 * 1024
# JSON and small forms. The largest ordinary bodies (a script, an article) are far below this.
DEFAULT_LIMIT = 4 * MB
# Room for the multipart wrapping and the form's other fields.
SLACK = MB


def _attachment_limit() -> int:
    return settings.max_upload_mb * MB + SLACK


# (path, largest body, needs a signed-in user before the body is read)
UPLOADS: list[tuple[re.Pattern, Callable[[], int], bool]] = [
    (re.compile(r"/api/tickets/\d+/attachments"), _attachment_limit, True),
    (re.compile(r"/api/kb/articles/\d+/files"), _attachment_limit, True),
    (re.compile(r"/api/purchase-orders/\d+/attachments"), _attachment_limit, True),
    # Twice the route's own 8 MB, so a slightly oversized image gets the route's clearer answer.
    (re.compile(r"/api/settings/branding/files/[^/]+"), lambda: 16 * MB + SLACK, True),
    (re.compile(r"/api/imports/kace/(preview|apply)"), lambda: kace.MAX_UPLOAD + SLACK, True),
    (re.compile(r"/api/imports/trackit"), lambda: trackit_jobs.MAX_UPLOAD + SLACK, True),
    (re.compile(r"/api/public/receiving/photo"), lambda: 15 * MB + SLACK, False),
]


def limit_for(path: str) -> tuple[int, bool]:
    for pattern, limit, signed_in in UPLOADS:
        if pattern.fullmatch(path):
            return limit(), signed_in
    return DEFAULT_LIMIT, False


class _TooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app, session_factory: Callable):
        self.app = app
        # Called per request, so tests can swap the database.
        self.session_factory = session_factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit, signed_in = limit_for(scope["path"])
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            return await _refuse(send, 413, _too_large(limit))
        if signed_in:
            try:
                await run_in_threadpool(self._check_signed_in, scope)
            except HTTPException as exc:
                return await _refuse(send, exc.status_code, exc.detail)

        seen = 0
        started = over = False

        async def counted():
            nonlocal seen, over
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:  # a chunked body, or a wrong Content-Length
                    over = True
                    raise _TooLarge()
            return message

        async def tracked(message):
            nonlocal started
            if over and not started:
                return  # the app's own answer to the broken read (a 400) gives way to ours
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except _TooLarge:
            if started:
                raise
        if over and not started:
            await _refuse(send, 413, _too_large(limit))

    def _check_signed_in(self, scope) -> None:
        from app import auth

        request = Request(scope)
        with self.session_factory()() as session:
            auth.current_user(request, session, request.headers.get("x-dev-user"))


def _too_large(limit: int) -> str:
    return f"Too large: up to {limit // MB} MB is accepted here"


async def _refuse(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})
