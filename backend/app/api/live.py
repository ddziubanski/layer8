# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /api/live: Server-Sent Events saying what changed (ids only); see app/live.py."""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import live
from app.auth import current_user, require_agent
from app.db import get_session_factory

router = APIRouter(tags=["system"])


def _check(factory, request: Request, x_dev_user: str | None) -> int:
    # A short session of its own: a stream lasts hours and mustn't hold a pooled connection.
    with factory() as session:
        return require_agent(current_user(request, session, x_dev_user)).id


@router.get("/api/live", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
async def live_updates(request: Request, factory=Depends(get_session_factory), x_dev_user: Annotated[str | None, Header()] = None):
    """Events: `data: {"t": "ticket"|"device"|"outage", "id": n}`. Staff only. Re-checks the sign-in every minute."""
    await asyncio.to_thread(_check, factory, request, x_dev_user)

    async def stream():
        q = live.hub.subscribe()
        loop = asyncio.get_running_loop()
        checked = loop.time()
        try:
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                if loop.time() - checked > live.RECHECK_SECONDS:
                    try:
                        await asyncio.to_thread(_check, factory, request, x_dev_user)
                    except HTTPException:
                        yield "event: signed-out\ndata: {}\n\n"
                        return
                    checked = loop.time()
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=live.KEEPALIVE_SECONDS)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            live.hub.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
