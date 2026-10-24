# SPDX-License-Identifier: AGPL-3.0-or-later
"""{first_name}, {name}, {ticket}, {subject}, {technician}, {queue} and {portal} in text a technician
or an admin wrote (canned replies, requester emails), filled in from a ticket. Anything else in
braces is left as it is."""

import re

from app import numbering
from app.config import settings

NAMES = ("first_name", "name", "ticket", "subject", "technician", "queue", "portal")
PATTERN = re.compile(r"\{(" + "|".join(NAMES) + r")\}")


def values(ticket, technician=None) -> dict[str, str]:
    name = ticket.requester.display_name if ticket.requester else ""
    tech = technician if technician is not None else ticket.assignee
    return {
        "first_name": name.split()[0] if name.split() else name,
        "name": name,
        "ticket": numbering.label(ticket.id, ticket.created_at),
        "subject": ticket.subject,
        "technician": tech.display_name if tech is not None else "the service desk",
        "queue": ticket.queue.name if ticket.queue else "",
        "portal": f"{settings.portal_url.rstrip('/')}/tickets/{ticket.id}" if settings.portal_url else "",
    }


def fill(text: str, found: dict[str, str]) -> str:
    return PATTERN.sub(lambda m: found[m.group(1)], text)
