"""Background worker: email (read the intake mailbox, send queued email), directory sync,
scheduled ticket rules and housekeeping, as jobs (app/jobs.py) that admins watch and
control on Settings > Status.

    python -m app.worker          # run until stopped (as a service)
    python -m app.worker --once   # run every job once, for testing a setup

Exits with status 75 when an admin asks for a restart; the service manager (systemd
Restart=always, or dev/worker.sh) starts it again.
"""

import argparse
import logging
import signal
import sys
import threading

from app import jobs, syslog
from app.config import settings
from app.db import SessionLocal, engine
from app.mail import graph
from app.mail.intake import IntakeConfig
from app.mail.types import Mailbox

log = logging.getLogger("app.worker")


class MailSetup:
    """The mailbox as Settings > Email describes it, rebuilt when those settings change."""

    def __init__(self):
        self._seen: tuple | None = None
        self.mailbox: Mailbox | None = None

    def update(self) -> tuple[Mailbox | None, IntakeConfig]:
        current = tuple(getattr(settings, n) for n in graph.MAIL_SETTINGS)
        if current != self._seen:
            self._seen = current
            self.mailbox = None
            if not settings.mailbox:
                log.info("No mailbox set (Settings > Email): email intake and sending are off", extra={"source": "email"})
            elif graph.missing(settings):
                log.warning("Email is set up partly; still needed in Settings > Email: %s", ", ".join(graph.missing(settings)), extra={"source": "email"})
            else:
                try:
                    self.mailbox = graph.from_settings(settings)
                    log.info("Watching %s", settings.mailbox, extra={"source": "email"})
                except Exception:
                    # E.g. an unknown tenant: retried when the settings change.
                    log.exception("Can't use the mailbox with these settings", extra={"source": "email"})
            if self.mailbox and not settings.mail_allowed_domains:
                log.warning("No 'Accept email from' domains: only senders who are already users will get tickets", extra={"source": "email"})
        return self.mailbox, IntakeConfig.create(settings.mailbox, settings.mail_allowed_domains)


def make_worker(session_factory=None) -> jobs.Worker:
    factory = session_factory or SessionLocal
    return jobs.Worker(factory, jobs.build_jobs(factory, MailSetup()))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="Run every job once and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    syslog.install(engine, "worker")

    worker = make_worker()
    if args.once:
        worker.tick(run_all=True)
        return

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("Started", extra={"source": "worker"})
    while not stop.is_set():
        try:
            if not worker.tick():
                sys.exit(jobs.RESTART_EXIT)
        except Exception:
            # E.g. the database restarting: log it and keep going.
            log.exception("Worker tick failed", extra={"source": "worker"})
        stop.wait(jobs.TICK_SECONDS)
    log.info("Stopped", extra={"source": "worker"})


if __name__ == "__main__":
    main()
