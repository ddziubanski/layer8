# SPDX-License-Identifier: AGPL-3.0-or-later
"""Update everyone affected: one message from a parent ticket to the requesters of it and its open
child tickets (the reports linked as the same problem), optionally resolving them all.

The preview runs the real thing inside a transaction and rolls it back, so it shows exactly what
would happen (and any reason it can't) without sending anything; emails wait in the outbox until a
commit. Applying needs the preview's fingerprint, so what's sent is what was reviewed. Only tickets
the technician may see are included, and only the message typed here is sent: nothing from internal
notes or other tickets. Each ticket gets the reply as its own public comment and history; a
requester with several of these tickets is emailed once.
"""
import hashlib

from sqlalchemy import select

from app import numbering, services, sla
from app.config import settings
from app.api.tickets import visible
from app.models import Ticket, TicketSource, TicketStatus, User


class BroadcastError(ValueError):
    pass


def _targets(session, parent: Ticket, user: User) -> tuple[list[Ticket], int]:
    """The parent and its open children the user may see, and how many children were left out
    because they're already resolved or closed (counted only among visible ones)."""
    children = session.scalars(visible(select(Ticket).where(Ticket.parent_id == parent.id), user).order_by(Ticket.id)).all()
    open_children = [c for c in children if c.status not in sla.CLOSED_STATUSES]
    targets = open_children + ([parent] if parent.status not in sla.CLOSED_STATUSES else [])
    return targets, len(children) - len(open_children)


def _emailable(user: User) -> bool:
    return bool(settings.mailbox) and bool(user.email) and user.is_active and not user.email.lower().endswith(".invalid")


def fingerprint(parent: Ticket, ids: list[int], body: str, resolve: bool, resolution_type_id: int | None, closure_reason_id: int | None = None) -> str:
    raw = f"{parent.id}\0{','.join(map(str, sorted(ids)))}\0{resolve}\0{resolution_type_id}\0{closure_reason_id}\0{body}"
    return hashlib.sha256(raw.encode()).hexdigest()


def run(session, parent: Ticket, user: User, *, body: str, resolve: bool, resolution_type_id: int | None = None,
        closure_reason_id: int | None = None, apply: bool = False, expected: str | None = None) -> dict:
    body = body.strip()
    if not body:
        raise BroadcastError("Write the update first")
    targets, skipped = _targets(session, parent, user)
    if len(targets) < 2:
        raise BroadcastError("Link the other reports as child tickets first: there's nobody else to update")
    ids = [t.id for t in targets]
    print_ = fingerprint(parent, ids, body, resolve, resolution_type_id, closure_reason_id)
    if apply and expected != print_:
        raise BroadcastError("The tickets or the message changed since the preview; preview again")
    # Open children outside this update (another queue the technician can't see) keep the parent
    # open. Said without naming or counting them.
    hidden_open = bool(session.scalar(select(Ticket.id).where(Ticket.parent_id == parent.id, Ticket.id.not_in(ids),
                                                             Ticket.status.not_in(sla.CLOSED_STATUSES)).limit(1)))
    savepoint = None if apply else session.begin_nested()
    emailed: set[int] = set()
    rows = []
    try:
        # Children before the parent, so a parent with open children can still be resolved.
        for t in targets:
            requester = session.get(User, t.requester_id)
            email = requester is not None and requester.id not in emailed and _emailable(requester)
            services.add_comment(session, t, user, body=body, is_internal=False, source=TicketSource.agent, email_requester=email)
            if email:
                emailed.add(requester.id)
            left_open = resolve and t.id == parent.id and hidden_open
            if resolve and not left_open:
                changes = {"status": TicketStatus.resolved}
                if resolution_type_id is not None:
                    changes["resolution_type_id"] = resolution_type_id
                if closure_reason_id is not None:
                    changes["closure_reason_id"] = closure_reason_id
                try:
                    services.update_ticket(session, t, user, changes)
                except services.ServiceError as exc:
                    raise BroadcastError(f"{numbering.label(t.id, t.created_at)}: {exc}") from None
            rows.append({"ticket": t, "requester": requester.display_name if requester else "", "emailed": email,
                         "resolved": resolve and not left_open,
                         "note": "Left open: it has other open child tickets that aren't part of this update" if left_open else None,
                         "why_no_email": None if email else ("email isn't set up (Settings > Mailbox)" if not settings.mailbox
                                                              else "already emailed for another of these tickets" if requester and requester.id in emailed
                                                              else "no email address, or the account is inactive")})
        session.flush()
        result = {"fingerprint": print_, "tickets": rows, "emails": len(emailed), "skipped_closed": skipped, "resolve": resolve}
        if apply:
            session.commit()
        return result
    finally:
        if savepoint is not None:
            savepoint.rollback()
            session.expire_all()
