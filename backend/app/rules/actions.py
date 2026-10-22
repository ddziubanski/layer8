"""Rule actions, applied through the normal services so history, SLA and notifications
behave exactly as if a person had done it.

Each action is {"type": ..., ...}. Text fields are templates: ${ticket_id}, ${subject},
${status}, ${priority}, ${queue}, ${category}, ${requester}, ${requester_email},
${assignee}, ${url}.
"""

from dataclasses import dataclass, field
from string import Template

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.config import settings
from app.mail import outbox
from app.models import Priority, Queue, Ticket, TicketSource, TicketStatus, User

ACTIONS: dict[str, dict] = {
    "set_status": {"label": "Set status", "fields": {"value": [s.value for s in TicketStatus]}},
    "set_priority": {"label": "Set priority", "fields": {"value": [p.value for p in Priority]}},
    "set_category": {"label": "Set category", "fields": {"value": "text"}},
    "set_queue": {"label": "Move to queue", "fields": {"value": "queue_id", "note": "template"}},
    "set_assignee": {"label": "Assign to (blank: unassign)", "fields": {"value": "user_id"}},
    "add_note": {"label": "Add internal note", "fields": {"body": "template"}},
    "add_reply": {"label": "Reply to requester (emailed)", "fields": {"body": "template"}},
    "email": {
        "label": "Send email",
        "fields": {"to": ["requester", "assignee", "queue_owners", "queue_managers", "address"], "address": "text", "subject": "template", "body": "template"},
    },
    "escalate": {"label": "Escalate", "fields": {"reason": "template"}},
    # For whom: a name (template; empty: the requester). A checklist that disables an AD account
    # needs a date and an AD person, so it's started by hand.
    "start_checklist": {"label": "Start a checklist", "fields": {"value": "checklist_id", "for_name": "template"}},
}
TEMPLATE_VARS = ["ticket_id", "subject", "status", "priority", "queue", "category", "requester", "requester_email", "assignee", "url"]


class ActionError(ValueError):
    pass


def validate(actions: list[dict]) -> None:
    for i, a in enumerate(actions, 1):
        kind = a.get("type")
        spec = ACTIONS.get(kind)
        if spec is None:
            raise ActionError(f"Action {i}: unknown type {kind!r}")
        for name, allowed in spec["fields"].items():
            value = a.get(name)
            if isinstance(allowed, list) and name in ("value", "to") and value not in allowed:
                raise ActionError(f"Action {i}: {name} must be one of {', '.join(allowed)}")
        if kind in ("add_note", "add_reply") and not str(a.get("body") or "").strip():
            raise ActionError(f"Action {i}: needs a body")
        if kind == "set_queue" and not isinstance(a.get("value"), int):
            raise ActionError(f"Action {i}: pick a queue")
        if kind == "email":
            if not str(a.get("subject") or "").strip() or not str(a.get("body") or "").strip():
                raise ActionError(f"Action {i}: email needs a subject and body")
            if a.get("to") == "address" and "@" not in str(a.get("address") or ""):
                raise ActionError(f"Action {i}: email needs an address")
        if kind == "start_checklist" and not isinstance(a.get("value"), int):
            raise ActionError(f"Action {i}: pick a checklist")
        if kind == "escalate" and not str(a.get("reason") or "").strip():
            raise ActionError(f"Action {i}: escalation needs a reason")


def render(text: str, ticket: Ticket) -> str:
    url = f"{settings.portal_url.rstrip('/')}/tickets/{ticket.id}" if settings.portal_url else ""
    values = {
        "ticket_id": ticket.id,
        "subject": ticket.subject,
        "status": ticket.status.value,
        "priority": ticket.priority.value,
        "queue": ticket.queue.name,
        "category": ticket.category or "",
        "requester": ticket.requester.display_name,
        "requester_email": ticket.requester.email,
        "assignee": ticket.assignee.display_name if ticket.assignee else "unassigned",
        "url": url,
    }
    # Unknown $names are left as typed rather than failing the rule.
    return Template(text).safe_substitute(values)


@dataclass
class Outcome:
    notes: int = 0
    emails: int = 0
    skipped: list[str] = field(default_factory=list)


def apply(session: Session, ticket: Ticket, action: dict, actor: User, out: Outcome) -> None:
    kind = action["type"]
    match kind:
        case "set_status":
            services.update_ticket(session, ticket, actor, {"status": TicketStatus(action["value"])})
        case "set_priority":
            services.update_ticket(session, ticket, actor, {"priority": Priority(action["value"])})
        case "set_category":
            services.update_ticket(session, ticket, actor, {"category": action.get("value") or None})
        case "set_queue":
            if ticket.queue_id != action["value"]:
                services.assign(session, ticket, actor, queue_id=action["value"], note=render(action.get("note") or "", ticket) or None)
                out.notes += 1
        case "set_assignee":
            if action.get("value"):
                services.assign(session, ticket, actor, assignee_id=action["value"])
            else:
                services.assign(session, ticket, actor, unassign=True)
        case "add_note":
            services.add_comment(session, ticket, actor, body=render(action["body"], ticket), is_internal=True, source=TicketSource.api)
            out.notes += 1
        case "add_reply":
            # Emailed to the requester by the normal agent-reply notification.
            services.add_comment(session, ticket, actor, body=render(action["body"], ticket), is_internal=False, source=TicketSource.api)
            out.emails += 1 if settings.mailbox else 0
        case "email":
            subject, body = render(action["subject"], ticket), render(action["body"], ticket)
            for to in _recipients(session, ticket, action):
                if outbox.rule_email(session, ticket, to, subject, body, to_requester=action["to"] == "requester"):
                    out.emails += 1
        case "start_checklist":
            _start_checklist(session, ticket, action, actor, out)
        case "escalate":
            if ticket.status in (TicketStatus.resolved, TicketStatus.closed):
                out.skipped.append("escalate: ticket is resolved or closed")
                return
            services.escalate(session, ticket, actor, reason=render(action["reason"], ticket))


def _start_checklist(session: Session, ticket: Ticket, action: dict, actor: User, out: Outcome) -> None:
    from app import checklists
    from app.models import ChecklistItem, ChecklistTemplate, TicketChecklist

    template = session.get(ChecklistTemplate, action["value"])
    if template is None or not template.is_active:
        out.skipped.append("start checklist: that checklist is gone or not in use")
        return
    if session.scalar(select(ChecklistItem.id).where(ChecklistItem.child_ticket_id == ticket.id)) is not None:
        # A checklist's own step: starting more from it could go on forever.
        out.skipped.append("start checklist: this ticket is a step of a checklist")
        return
    if session.scalar(select(TicketChecklist.id).where(TicketChecklist.ticket_id == ticket.id, TicketChecklist.template_id == template.id)) is not None:
        out.skipped.append(f"start checklist: {template.name} is already on this ticket")
        return
    if any(i.get("action") for i in template.items):
        out.skipped.append(f"start checklist: {template.name} disables an AD account, so it needs a date and a person: start it by hand")
        return
    for_name = render(action.get("for_name") or "", ticket).strip() or ticket.requester.display_name
    checklists.start(session, ticket, actor, template, key_date=None, for_user=None, for_name=for_name)
    out.notes += 1


def _recipients(session: Session, ticket: Ticket, action: dict) -> list[User | str]:
    match action["to"]:
        case "requester":
            return [ticket.requester]
        case "assignee":
            return [ticket.assignee] if ticket.assignee else []
        case "queue_owners":
            return list(session.get(Queue, ticket.queue_id).owners)
        case "queue_managers":
            return list(session.get(Queue, ticket.queue_id).managers)
        case "address":
            return [str(action["address"]).strip()]
    return []
