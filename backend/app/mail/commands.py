"""Tickets Anywhere: technicians work tickets by email.

In a reply to a ticket's email (or a new email to the service desk), a technician's lines that
start with #word are commands, done as if in the console and noted on the ticket "by email":

    #close [reason]    #resolve [resolution type]    #reopen    #status waiting
    #assign me | dcole | dcole@corp.local | "Dana Cole"    #unassign
    #priority high     #queue Network    #category Printers
    #internal          (this reply is an internal note, not sent to the requester)
    #create  #requester someone@corp.local   (new email: log a ticket for someone else)

Only staff who may see the ticket's queue can use them; anyone else's #lines are just text.
Anything that can't be done comes back to the technician by email.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import fields, services
from app.models import FieldList, FieldOption, Priority, Queue, Ticket, TicketStatus, User, UserRole

LINE = re.compile(r"^\s*#([a-z][a-z_-]*)\b[ \t]*(.*?)\s*$", re.IGNORECASE)
STATUSES = {
    "new": TicketStatus.new, "open": TicketStatus.open, "waiting": TicketStatus.waiting_on_user, "waiting_on_user": TicketStatus.waiting_on_user,
    "resolved": TicketStatus.resolved, "closed": TicketStatus.closed,
}
HELP = "Commands: #close, #resolve [type], #reopen, #status open|waiting|resolved|closed, #assign me|name, #unassign, #priority low|medium|high|critical, #queue <name>, #category <text>, #internal."


@dataclass
class Parsed:
    text: str
    commands: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Result:
    done: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    internal: bool = False


def parse(body: str) -> Parsed:
    """Pull command lines out of a message's new text."""
    keep, commands = [], []
    for line in body.splitlines():
        m = LINE.match(line)
        if m:
            commands.append((m.group(1).lower().replace("-", "_"), m.group(2).strip().strip('"')))
        else:
            keep.append(line)
    return Parsed("\n".join(keep).strip(), commands)


def _staff(session: Session, who: str, sender: User) -> User | None:
    if who.lower() == "me":
        return sender
    w = who.lower()
    staff = select(User).where(User.is_active, User.role.in_([UserRole.agent, UserRole.admin]))
    return (
        session.scalar(staff.where(func.lower(User.email) == w))
        or session.scalar(staff.where(func.split_part(func.lower(User.email), "@", 1) == w))
        or session.scalar(staff.where(func.lower(User.display_name) == w))
    )


def _option(session: Session, lst: FieldList, label: str) -> FieldOption | None:
    return session.scalar(select(FieldOption).where(FieldOption.list == lst, FieldOption.is_active, func.lower(FieldOption.label) == label.lower()))


def apply(session: Session, ticket: Ticket, sender: User, commands: list[tuple[str, str]]) -> Result:
    """Do each command on the ticket as `sender`; what can't be done is collected, not raised."""
    r = Result()
    for name, arg in commands:
        try:
            if name in ("internal", "private", "note"):
                r.internal = True
                r.done.append("kept as an internal note")
            elif name in ("public", "reply"):
                r.internal = False
            elif name in ("close", "resolve", "resolved", "closed"):
                status = TicketStatus.closed if name.startswith("close") else TicketStatus.resolved
                changes: dict = {"status": status}
                if arg:
                    rt = _option(session, FieldList.resolution_type, arg)
                    cr = _option(session, FieldList.closure_reason, arg)
                    if rt is None and cr is None:
                        raise services.ServiceError(f"no resolution type or closure reason called {arg!r}")
                    if rt:
                        changes["resolution_type_id"] = rt.id
                    if cr:
                        changes["closure_reason_id"] = cr.id
                services.update_ticket(session, ticket, sender, changes)
                r.done.append(f"{status.value}" + (f" ({arg})" if arg else ""))
            elif name == "reopen":
                services.update_ticket(session, ticket, sender, {"status": TicketStatus.open})
                r.done.append("reopened")
            elif name == "status":
                status = STATUSES.get(arg.lower().replace(" ", "_"))
                if status is None:
                    raise services.ServiceError(f"unknown status {arg!r} (new, open, waiting, resolved, closed)")
                services.update_ticket(session, ticket, sender, {"status": status})
                r.done.append(f"status {status.value.replace('_', ' ')}")
            elif name == "assign":
                if not arg:
                    raise services.ServiceError("say who: #assign me, or a name or email")
                who = _staff(session, arg, sender)
                if who is None:
                    raise services.ServiceError(f"no technician called {arg!r}")
                services.assign(session, ticket, sender, assignee_id=who.id, note="by email")
                r.done.append(f"assigned to {who.display_name}")
            elif name == "unassign":
                services.assign(session, ticket, sender, unassign=True, note="by email")
                r.done.append("unassigned")
            elif name == "priority":
                try:
                    p = Priority(arg.lower())
                except ValueError:
                    raise services.ServiceError(f"unknown priority {arg!r} (low, medium, high, critical)") from None
                services.update_ticket(session, ticket, sender, {"priority": p})
                r.done.append(f"priority {p.value}")
            elif name == "queue":
                q = session.scalar(select(Queue).where(Queue.is_active, func.lower(Queue.name) == arg.lower()))
                if q is None:
                    raise services.ServiceError(f"no queue called {arg!r}")
                services.assign(session, ticket, sender, queue_id=q.id, note="by email")
                r.done.append(f"moved to {q.name}")
            elif name == "category":
                services.update_ticket(session, ticket, sender, {"category": arg or None})
                r.done.append(f"category {arg or '(none)'}")
            elif name in ("create", "requester"):
                pass  # handled when the ticket is made (new emails only)
            else:
                raise services.ServiceError(f"unknown command #{name}")
        except (services.ServiceError, fields.FieldError) as exc:
            r.errors.append(f"#{name}{(' ' + arg) if arg else ''}: {exc}")
    return r
