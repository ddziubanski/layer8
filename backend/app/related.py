# SPDX-License-Identifier: AGPL-3.0-or-later
"""Related open tickets: other reports of the same problem, suggested on a ticket's Work tab.

A ticket is suggested when it shares a device, a system or a Nutanix VM with this one, or at least
two meaningful words in their subjects. Only open tickets in queues the technician may see are
considered, before anything is ranked or counted; this ticket and those already linked to it
(parent, children, siblings) are left out. Nothing is linked automatically: a technician links one
with the existing parent/child relationship.
"""
import re

from sqlalchemy import func, select

from app import search
from app.api.tickets import visible
from app.models import Device, NutanixVm, System, Ticket, TicketDevice, TicketNutanixVm, TicketStatus, TicketSystem, User

LIMIT = 5
# Too common in ticket subjects to say two tickets are about the same thing.
STOPWORDS = set("""
a an and are as at be but by can cant could do does doesnt dont for from get getting got has have help how i im in into is isnt
it its me my need needs new no not of on or our please re fw fwd still that the their there this to too up urgent was we were
what when where which who why will with wont work working would you your issue issues problem problems request ticket error
""".split())
OPEN = [s for s in TicketStatus if s not in (TicketStatus.resolved, TicketStatus.closed)]


def words(subject: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9._-]*[a-z0-9]|[a-z0-9]{3,}", subject.lower()) if len(w) >= 3 and w not in STOPWORDS}


def suggestions(session, ticket: Ticket, user: User) -> list[dict]:
    linked = {ticket.id, *(t for t in [ticket.parent_id] if t)}
    linked |= set(session.scalars(select(Ticket.id).where(Ticket.parent_id == ticket.id)))
    if ticket.parent_id:
        linked |= set(session.scalars(select(Ticket.id).where(Ticket.parent_id == ticket.parent_id)))
    candidates = visible(select(Ticket.id).where(Ticket.status.in_(OPEN), Ticket.id.not_in(linked)), user)
    reasons: dict[int, list[str]] = {}
    score: dict[int, int] = {}

    def add(tid: int, why: str, points: int) -> None:
        reasons.setdefault(tid, []).append(why)
        score[tid] = score.get(tid, 0) + points

    shared = (
        (TicketDevice, TicketDevice.device_id, Device, Device.id, Device.name, "Same device"),
        (TicketSystem, TicketSystem.system_id, System, System.id, System.name, "Same system"),
        (TicketNutanixVm, TicketNutanixVm.vm_id, NutanixVm, NutanixVm.ext_id, NutanixVm.name, "Same VM"),
    )
    for link, key, thing, thing_key, name, label in shared:
        mine = select(key).where(link.ticket_id == ticket.id)
        for tid, what in session.execute(
            select(link.ticket_id, name).join(thing, thing_key == key).where(key.in_(mine), link.ticket_id.in_(candidates))
        ):
            add(tid, f"{label}: {what}", 3)

    mine = words(ticket.subject)
    if len(mine) >= 2:
        # Any of the words, through the search index; two in common are required below.
        query = func.websearch_to_tsquery(search.CONFIG, " or ".join(sorted(mine)))
        found = session.execute(
            select(Ticket.id, Ticket.subject).where(search.TICKET_TEXT.op("@@")(query), Ticket.id.in_(candidates)).limit(500)
        )
        for tid, subject in found:
            common = sorted(mine & words(subject))
            if len(common) >= 2:
                add(tid, "Subject shares: " + ", ".join(common[:5]), len(common))

    if not score:
        return []
    best = sorted(score, key=lambda tid: (-score[tid], -tid))[:LIMIT]
    tickets = {t.id: t for t in session.scalars(select(Ticket).where(Ticket.id.in_(best)))}
    return [{"ticket": tickets[tid], "reasons": reasons[tid]} for tid in best if tid in tickets]

