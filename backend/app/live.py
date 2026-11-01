# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live updates: open browsers hear that something changed the moment it's committed, instead of
waiting for their next poll.

- Any commit that adds or changes a ticket, its comments or history, a device event or an outage
  sends a Postgres NOTIFY on CHANNEL with a tiny payload: {"t": "ticket", "id": 4821}. NOTIFY is
  transactional: a rolled-back change sends nothing. Ids only: the browser fetches what it shows
  through the normal API, so permissions stay in one place.
- Each API process keeps one listening connection (Hub) and fans notifications out to its
  Server-Sent Events streams (GET /api/live). Streams re-check the sign-in every minute and send a
  keep-alive comment every 25 seconds (the proxy's read timeout is 300 s).
- The browser falls back to its usual polling when the stream isn't available.
"""
import asyncio
import json
import logging
import threading
import time

from sqlalchemy import event, text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

CHANNEL = "layer8_live"
KEEPALIVE_SECONDS = 25
RECHECK_SECONDS = 60


def install() -> None:
    """Send NOTIFY for what each flush touched, inside the same transaction."""
    if getattr(install, "done", False):
        return
    install.done = True

    @event.listens_for(Session, "after_flush")
    def _notify(session, flush_context):
        try:
            bind = session.get_bind()
            if bind.dialect.name != "postgresql":
                return
            sent = session.info.setdefault("live_sent", set())
            for payload in _payloads_after(session, flush_context) - sent:
                session.connection().execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})
                sent.add(payload)
        except Exception:  # Never let live updates break a write.
            log.debug("live notify skipped", exc_info=True)

    @event.listens_for(Session, "after_transaction_end")
    def _reset(session, transaction):
        if transaction.parent is None:
            session.info.pop("live_sent", None)


def _payloads_after(session: Session, flush_context) -> set[str]:
    """What this flush wrote, as notification payloads (ids are assigned by now)."""
    from app.models import Comment, DeviceEvent, NetworkOutage, Ticket, TicketCheck, TicketEvent

    out: set[str] = set()
    for state in list(flush_context.states):
        obj = state.obj()
        if obj is None:
            continue
        if isinstance(obj, Ticket) and obj.id is not None:
            out.add(json.dumps({"t": "ticket", "id": obj.id}))
        elif isinstance(obj, (Comment, TicketEvent, TicketCheck)) and getattr(obj, "ticket_id", None) is not None:
            out.add(json.dumps({"t": "ticket", "id": obj.ticket_id}))
        elif isinstance(obj, DeviceEvent) and obj.device_id is not None:
            out.add(json.dumps({"t": "device", "id": obj.device_id}))
        elif isinstance(obj, NetworkOutage):
            out.add(json.dumps({"t": "outage", "id": obj.ticket_id or 0}))
    return out


class Hub:
    """One LISTEN connection per process, shared by every stream in it."""

    def __init__(self):
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.add((asyncio.get_running_loop(), q))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._listen, name="layer8-live", daemon=True)
                self._thread.start()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def publish(self, payload: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            loop.call_soon_threadsafe(_offer, q, payload)

    def _listen(self) -> None:
        import psycopg

        from app.config import settings

        url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        delay = 1
        while True:
            with self._lock:
                if not self._subs:
                    self._thread = None
                    return
            try:
                with psycopg.connect(url, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    delay = 1
                    while True:
                        for n in conn.notifies(timeout=30):
                            self.publish(n.payload)
                        with self._lock:
                            if not self._subs:
                                self._thread = None
                                return
            except Exception:
                log.warning("Live updates: listener lost its database connection; retrying in %ss", delay, exc_info=True)
                time.sleep(delay)
                delay = min(delay * 2, 60)


def _offer(q: asyncio.Queue, payload: str) -> None:
    if q.full():
        # A slow browser: drop the oldest; it refetches on any event anyway.
        q.get_nowait()
    q.put_nowait(payload)


hub = Hub()
