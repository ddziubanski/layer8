# SPDX-License-Identifier: AGPL-3.0-or-later
"""One mail pass: read the intake mailbox, then send queued email. The worker runs it on a
timer; admins can run it on demand (Settings > Email > Check mailbox now).

Only one pass runs at a time across every process, so two can't pick up the same message.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import sla
from app.mail import outbox
from app.mail.intake import IntakeConfig, Outcome, run_intake
from app.mail.types import Mailbox

# Any constant works; it just has to be the same in every process.
MAIL_LOCK = 724_002


@dataclass
class MailPass:
    outcomes: list[Outcome]
    sent: int


def run_pass(session_factory: Callable[[], Session], mailbox: Mailbox, cfg: IntakeConfig) -> MailPass | None:
    """None if another pass is running right now."""
    with session_factory() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": MAIL_LOCK}):
            return None
        try:
            outcomes = run_intake(session_factory, mailbox, cfg)
            with session_factory() as session:
                sent = outbox.send_due(session, mailbox, sla.utcnow())
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MAIL_LOCK})
    return MailPass(outcomes, sent)
