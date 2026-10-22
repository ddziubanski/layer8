"""Checklists (onboarding, offboarding...): a template's steps become child tickets of the ticket
they're started on, each due a number of days from the checklist's date, in its own queue or with
its own technician. An offboarding step can also disable the leaver's AD account on its day."""

from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app import adchanges, numbering, runs, services
from app.models import ChecklistItem, ChecklistTemplate, Queue, Ticket, TicketChecklist, TicketSource, TicketStatus, User, UserRole

ACTIONS = {"disable_ad_account": "Disable the AD account on the day"}


class ChecklistError(ValueError):
    """The message is safe to show."""


def _end_of(day: date) -> datetime:
    return datetime.combine(day, time(23, 59)).astimezone()


def due_on(key_date: date | None, offset_days: int) -> date | None:
    return key_date + timedelta(days=offset_days) if key_date else None


def start(
    session: Session, ticket: Ticket, actor: User, template: ChecklistTemplate, *, key_date: date | None, for_user: User | None, for_name: str,
) -> TicketChecklist:
    """Make the steps' child tickets (and any actions), and note it on the ticket. Doesn't commit."""
    if not template.is_active:
        raise ChecklistError("This checklist isn't in use")
    if not template.items:
        raise ChecklistError("This checklist has no steps")
    for_name = (for_name or (for_user.display_name if for_user else "")).strip()
    if not for_name:
        raise ChecklistError("Say who it's for")
    if any(i.get("action") == "disable_ad_account" for i in template.items):
        if for_user is None or not for_user.directory_id:
            raise ChecklistError("Disabling an AD account needs the person to be someone from AD")
        if key_date is None:
            raise ChecklistError("Give the last day: the account is disabled from a date")
    checklist = TicketChecklist(
        ticket_id=ticket.id, template_id=template.id, name=template.name, kind=template.kind, for_name=for_name,
        for_user_id=for_user.id if for_user else None, key_date=key_date, started_by_id=actor.id,
    )
    session.add(checklist)
    made = []
    for pos, step in enumerate(template.items):
        offset = int(step.get("offset_days") or 0)
        due = due_on(key_date, offset)
        queue = session.get(Queue, step["queue_id"]) if step.get("queue_id") else None
        lines = [step.get("details") or "", "", f"Part of \"{template.name}\" for {for_name} ({numbering.label(ticket.id, ticket.created_at)} {ticket.subject})."]
        if due:
            lines.append(f"Due {due:%A %d %B %Y}.")
        child = services.create_ticket(
            session, subject=f"{step['title']}: {for_name}"[:300], description="\n".join(lines).strip(), category=None,
            # The step's queue, else the ticket's: chosen, so a routing rule only moves it if it may replace a choice.
            requester=ticket.requester, source=TicketSource.agent, actor=actor, requested_queue=queue or ticket.queue,
            parent=ticket,
        )
        if due:
            # The step's own date wins over the SLA's.
            child.resolution_due_at = _end_of(due)
        if step.get("assignee_id"):
            assignee = session.get(User, step["assignee_id"])
            if assignee is not None and assignee.is_active and assignee.role != UserRole.requester:
                services.assign(session, child, actor, assignee_id=assignee.id)
        item = ChecklistItem(position=pos, title=step["title"], offset_days=offset, due_on=due, child_ticket_id=child.id, action=step.get("action") or None)
        if item.action == "disable_ad_account":
            try:
                (run,) = runs.request(
                    session, adchanges.disable_script(session), actor, device_ids=[], on_runner=True, values={"user_guid": for_user.directory_id},
                    ticket=child, not_before=datetime.combine(due, time.min).astimezone(),
                )
            except runs.RunError as exc:
                raise ChecklistError(str(exc)) from None
            item.run_id = run.id
        checklist.items.append(item)
        made.append(child)
    when = f" ({'start date' if template.kind == 'onboarding' else 'last day' if template.kind == 'offboarding' else 'date'} {key_date:%d %B %Y})" if key_date else ""
    services.add_comment(
        session, ticket, actor, is_internal=True, source=TicketSource.agent, as_staff=True,
        body=f"Started checklist \"{template.name}\" for {for_name}{when}: " + ", ".join(f"{numbering.label(c.id, c.created_at)} {c.subject}" for c in made) + ".",
    )
    return checklist


def set_date(session: Session, checklist: TicketChecklist, actor: User, key_date: date) -> None:
    """Set or move the checklist's date: steps' due dates follow, and a waiting disable moves with them. Doesn't commit."""
    from app.models import RunStatus, ScriptRun

    old = checklist.key_date
    checklist.key_date = key_date
    for item in checklist.items:
        item.due_on = due_on(key_date, item.offset_days)
        child = item.child_ticket
        if child is not None and child.status not in (TicketStatus.resolved, TicketStatus.closed):
            child.resolution_due_at = _end_of(item.due_on)
        if item.run_id:
            run = session.get(ScriptRun, item.run_id)
            if run is not None and run.status in (RunStatus.awaiting_approval, RunStatus.queued):
                run.not_before = datetime.combine(item.due_on, time.min).astimezone()
    word = {"onboarding": "Start date", "offboarding": "Last day"}.get(checklist.kind, "Date")
    was = f" (was {old:%d %B %Y})" if old else ""
    ticket = session.get(Ticket, checklist.ticket_id)
    services.add_comment(
        session, ticket, actor, is_internal=True, source=TicketSource.agent, as_staff=True,
        body=f"{word} for \"{checklist.name}\" ({checklist.for_name}) set to {key_date:%A %d %B %Y}{was}; the steps' due dates follow.",
    )
