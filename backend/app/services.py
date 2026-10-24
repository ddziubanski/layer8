# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket operations. The API, the mailbox worker and the portal all go through here,
so routing, SLA and history behave the same whatever the source.

Each function changes the session and records TicketEvents; callers commit.
"""

from contextvars import ContextVar

from sqlalchemy import func, select, update

from app.config import settings
from sqlalchemy.orm import Session

from app import fields, routing, sla, storage
from app.mail import outbox
from app.priority import priority_for
from app.models import (
    Attachment,
    Comment,
    Device,
    EventType,
    FieldList,
    FieldOption,
    Impact,
    Priority,
    Queue,
    RoutingRule,
    SlaPolicy,
    Ticket,
    TicketEvent,
    TicketDevice,
    TicketSource,
    TicketStatus,
    User,
    Urgency,
    UserRole,
)

PRIORITY_ORDER = [Priority.low, Priority.medium, Priority.high, Priority.critical]
AGENT_ROLES = (UserRole.agent, UserRole.admin)
# The account rules act as. Inactive and passwordless, so it can't sign in or be assigned.
AUTOMATION_EMAIL = "automation@helpdesk.invalid"

# Set while a ticket rule runs, so every event it causes says which rule.
current_rule: ContextVar[str | None] = ContextVar("current_rule", default=None)


class ServiceError(Exception):
    """A request that is well-formed but not allowed in the current state (HTTP 409/422)."""


def _event(session: Session, ticket: Ticket, type_: EventType, actor: User | None, **data) -> None:
    if rule := current_rule.get():
        data["rule"] = rule
    session.add(TicketEvent(ticket_id=ticket.id, actor_id=actor.id if actor else None, type=type_, data=data))


def automation_user(session: Session) -> User:
    user = session.scalar(select(User).where(func.lower(User.email) == AUTOMATION_EMAIL))
    if user is None:
        user = User(email=AUTOMATION_EMAIL, display_name="Service Desk", role=UserRole.agent, is_active=False)
        session.add(user)
        session.flush()
    return user


def _policy_for(session: Session, priority: Priority) -> SlaPolicy | None:
    return session.scalar(select(SlaPolicy).where(SlaPolicy.priority == priority))


def _default_queue(session: Session) -> Queue:
    queue = session.scalar(select(Queue).where(Queue.is_default, Queue.is_active))
    if queue is None:
        raise ServiceError("No default queue is configured; create a queue with is_default=true")
    return queue


def _active_queue(session: Session, queue_id: int) -> Queue:
    queue = session.get(Queue, queue_id)
    if queue is None or not queue.is_active:
        raise ServiceError(f"Queue {queue_id} does not exist or is inactive")
    return queue


def _assignable_agent(session: Session, user_id: int, queue: Queue) -> User:
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.role not in AGENT_ROLES:
        raise ServiceError(f"User {user_id} is not an active agent")
    if not queue.can_be_assigned(user):
        raise ServiceError(f"{user.display_name} is not an owner of the {queue.name} queue")
    return user


def computer_for(session: Session, user: User) -> Device | None:
    """The person's computer when there's no doubt: their one active assigned device."""
    found = session.scalars(select(Device).where(Device.assigned_user_id == user.id, Device.is_active).limit(2)).all()
    return found[0] if len(found) == 1 else None


def create_ticket(
    session: Session,
    *,
    subject: str,
    description: str,
    category: str | None,
    requester: User,
    source: TicketSource,
    priority: Priority = Priority.medium,
    actor: User | None = None,
    requested_queue: Queue | None = None,
    preferred_queue: Queue | None = None,
    urgency: Urgency | None = None,
    impact: Impact | None = None,
    contact_phone: str | None = None,
    department: FieldOption | None = None,
    computer: Device | None = None,
    parent: Ticket | None = None,
) -> Ticket:
    """preferred_queue: where the requester's choices point (e.g. the queue a portal problem
    option sends to); used instead of requested_queue unless a rule may replace the choice.
    Contact phone and department default to the requester's (from AD)."""
    # The requester's impact and urgency set the starting priority; rules may still change it.
    if urgency is not None and impact is not None:
        priority = priority_for(session, impact, urgency)
    facts = routing.TicketFacts(
        subject=subject,
        description=description,
        category=category,
        requester_email=requester.email,
        source=source,
        priority=priority,
        requested_queue_id=requested_queue.id if requested_queue else None,
    )
    rule = routing.first_match(session.scalars(select(RoutingRule)), facts)

    # A requester's chosen queue stands unless the rule is allowed to replace it.
    if rule and rule.set_queue_id and (requested_queue is None or rule.may_replace_requested_queue()):
        queue = _active_queue(session, rule.set_queue_id)
    elif preferred_queue is not None or requested_queue is not None:
        queue = preferred_queue or requested_queue
    else:
        queue = _default_queue(session)
    final_priority = rule.set_priority if rule and rule.set_priority else priority
    created = sla.utcnow()

    ticket = Ticket(
        subject=subject,
        description=description,
        category=category,
        source=source,
        status=TicketStatus.new,
        priority=final_priority,
        requester=requester,
        queue=queue,
        requested_queue_id=requested_queue.id if requested_queue else None,
        matched_rule_id=rule.id if rule else None,
        matched_rule_name=rule.name if rule else None,
        created_at=created,
        updated_at=created,
        status_changed_at=created,
        urgency=urgency,
        impact=impact,
        contact_phone=contact_phone or None,
        department=department,
        computer=computer or computer_for(session, requester),
        parent=parent,
    )
    fields.fill_contact(session, ticket, requester)
    sla.apply_policy(ticket, _policy_for(session, final_priority), created)
    ticket.unassigned_since = created
    session.add(ticket)
    session.flush()
    if parent is not None:
        _event(session, parent, EventType.child_changed, actor, child=ticket.id, subject=ticket.subject, action="added")

    _event(
        session,
        ticket,
        EventType.created,
        actor,
        source=source,
        requested_priority=priority,
        urgency=urgency.value if urgency else None,
        impact=impact.value if impact else None,
    )
    _event(
        session,
        ticket,
        EventType.routed,
        None,
        rule_id=rule.id if rule else None,
        rule_name=rule.name if rule else None,
        queue_id=queue.id,
        queue_name=queue.name,
        requested_queue_id=requested_queue.id if requested_queue else None,
        priority=final_priority,
    )
    # No acknowledgement when an agent logs a ticket for themselves.
    if not (actor is not None and actor.id == requester.id and source in (TicketSource.agent, TicketSource.api)):
        outbox.ticket_created(session, ticket)
    return ticket


def assign(
    session: Session,
    ticket: Ticket,
    actor: User,
    *,
    queue_id: int | None = None,
    assignee_id: int | None = None,
    unassign: bool = False,
    note: str | None = None,
) -> Ticket:
    """Move to a queue and/or set the assignee. Covers first assignment and reassignment."""
    before = {"queue_id": ticket.queue_id, "assignee_id": ticket.assignee_id}
    from_queue = ticket.queue

    if queue_id is not None:
        ticket.queue = _active_queue(session, queue_id)
    if unassign:
        ticket.assignee = None
    elif assignee_id is not None:
        ticket.assignee = _assignable_agent(session, assignee_id, ticket.queue)
    elif ticket.assignee is not None and not ticket.queue.can_be_assigned(ticket.assignee):
        # Moved to a queue the current assignee doesn't own.
        ticket.assignee = None

    session.flush()
    after = {"queue_id": ticket.queue_id, "assignee_id": ticket.assignee_id}
    if after == before:
        return ticket

    _event(session, ticket, EventType.assigned, actor, before=before, after=after)
    if ticket.assignee_id is None and before["assignee_id"] is not None:
        # Unassigned again: the wait for someone to take it starts over.
        ticket.unassigned_since, ticket.unaccepted_alerted_at = sla.utcnow(), None
    elif ticket.assignee_id is not None:
        ticket.unassigned_since = None
    if ticket.queue_id != from_queue.id:
        _move_note(session, ticket, actor, from_queue, ticket.queue, note)
    if ticket.assignee_id is not None and ticket.status == TicketStatus.new:
        _set_status(session, ticket, TicketStatus.open, actor)
    return ticket


def escalate(session: Session, ticket: Ticket, actor: User, *, reason: str) -> Ticket:
    """Raise priority one step and move to the queue's escalation queue, if it has one."""
    if ticket.status in sla.CLOSED_STATUSES:
        raise ServiceError("Cannot escalate a resolved or closed ticket")

    from_queue, from_priority = ticket.queue, ticket.priority
    idx = PRIORITY_ORDER.index(ticket.priority)
    to_priority = PRIORITY_ORDER[min(idx + 1, len(PRIORITY_ORDER) - 1)]
    to_queue = from_queue.escalation_queue if from_queue.escalation_queue_id else from_queue

    if to_queue.id != from_queue.id:
        ticket.queue = to_queue
        # The current assignee belongs to the old queue's team.
        ticket.assignee = None
        _move_note(session, ticket, actor, from_queue, to_queue, f"Escalated: {reason}")
    if to_priority != from_priority:
        _set_priority(session, ticket, to_priority)

    session.flush()
    _event(
        session,
        ticket,
        EventType.escalated,
        actor,
        reason=reason,
        from_queue_id=from_queue.id,
        to_queue_id=to_queue.id,
        from_priority=from_priority,
        to_priority=to_priority,
    )
    outbox.escalated(session, ticket, to_queue.managers, actor=actor, reason=reason)
    return ticket


def _move_note(session: Session, ticket: Ticket, actor: User, from_queue: Queue, to_queue: Queue, note: str | None) -> None:
    """Every queue move leaves an internal note on the ticket saying who moved it where."""
    body = f"Moved from {from_queue.name} to {to_queue.name} by {actor.display_name}."
    if note and note.strip():
        body += f"\n\n{note.strip()}"
    add_comment(session, ticket, actor, body=body, is_internal=True, source=TicketSource.agent)


def _set_priority(session: Session, ticket: Ticket, priority: Priority) -> None:
    ticket.priority = priority
    # Deadlines are always measured from creation, so raising priority tightens them.
    sla.apply_policy(ticket, _policy_for(session, priority), ticket.created_at)


def _set_status(session: Session, ticket: Ticket, status: TicketStatus, actor: User | None) -> None:
    old = ticket.status
    if old == status:
        return
    ticket.status = status
    ticket.status_changed_at = sla.utcnow()
    if status == TicketStatus.waiting_on_user:
        sla.pause(ticket, ticket.status_changed_at)
    elif old == TicketStatus.waiting_on_user:
        sla.resume(ticket, ticket.status_changed_at)
    if status == TicketStatus.resolved:
        ticket.resolved_at = sla.utcnow()
    elif status == TicketStatus.closed:
        ticket.closed_at = sla.utcnow()
        ticket.resolved_at = ticket.resolved_at or ticket.closed_at
    elif old in sla.CLOSED_STATUSES:
        # Reopened: it will be resolved again, maybe differently.
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.resolution_type = None
        ticket.closure_reason = None
    _event(session, ticket, EventType.status_changed, actor, before=old, after=status)
    if status in sla.CLOSED_STATUSES and old not in sla.CLOSED_STATUSES:
        from app import surveys

        # One survey per ticket at most: a reopened ticket solved again isn't asked about twice.
        surveys.maybe_send(session, ticket)
    # The parent hears when one of its child tickets is done.
    if ticket.parent_id is not None and status in sla.CLOSED_STATUSES and old not in sla.CLOSED_STATUSES:
        parent = session.get(Ticket, ticket.parent_id)
        who = actor.display_name if actor else "Automation"
        left = session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.parent_id == parent.id, Ticket.id != ticket.id, Ticket.status.not_in(list(sla.CLOSED_STATUSES)))
        )
        add_comment(
            session, parent, actor or automation_user(session), is_internal=True, source=TicketSource.agent, as_staff=True,
            body=f"Child ticket #{ticket.id} \"{ticket.subject}\" was {status.value} by {who}." + (f" {left} child ticket(s) still open." if left else " All child tickets are done."),
        )


# Ticket fields agents edit (beyond subject/description/category): name -> (dropdown list or None for text).
TRACKED_FIELDS = {
    "resolution_type_id": FieldList.resolution_type,
    "closure_reason_id": FieldList.closure_reason,
    "department_id": FieldList.department,
    "contact_phone": None,
}


def _option_label(session: Session, option_id: int | None) -> str | None:
    opt = session.get(FieldOption, option_id) if option_id is not None else None
    return opt.label if opt else None


def _set_tracked_fields(session: Session, ticket: Ticket, actor: User, changes: dict) -> None:
    edited = {}
    for name, lst in TRACKED_FIELDS.items():
        if name not in changes:
            continue
        new = changes.pop(name)
        old = getattr(ticket, name)
        if lst is None:
            new = (new or "").strip() or None
        elif new != old:  # re-sending what the ticket has is fine, even a since-retired choice
            try:
                fields.choice(session, lst, new)
            except fields.FieldError as exc:
                raise ServiceError(str(exc)) from None
        if new != old:
            label = name.removesuffix("_id")
            if lst is not None:
                edited[label] = {"before": _option_label(session, old), "after": _option_label(session, new)}
            else:
                edited[label] = {"before": old, "after": new}
            setattr(ticket, name, new)
    if edited:
        session.flush()
        session.expire(ticket, ["resolution_type", "closure_reason", "department"])
        _event(session, ticket, EventType.updated, actor, fields=edited)


def _set_computer(session: Session, ticket: Ticket, actor: User, device_id: int | None) -> None:
    if device_id == ticket.computer_id:
        return
    device = session.get(Device, device_id) if device_id is not None else None
    if device_id is not None and device is None:
        raise ServiceError("No such device")
    before = ticket.computer.name if ticket.computer else None
    ticket.computer = device
    _event(session, ticket, EventType.updated, actor, fields={"computer": {"before": before, "after": device.name if device else None}})


def check_children(session: Session, ticket: Ticket) -> None:
    """Refuse to resolve or close a ticket whose child tickets are still open (Settings > Ticket fields)."""
    if not settings.block_parent_close:
        return
    open_ids = session.scalars(
        select(Ticket.id).where(Ticket.parent_id == ticket.id, Ticket.status.not_in(list(sla.CLOSED_STATUSES))).order_by(Ticket.id)
    ).all()
    if open_ids:
        raise ServiceError(f"Child ticket(s) {', '.join(f'#{i}' for i in open_ids)} are still open: resolve them first")


def set_parent(session: Session, ticket: Ticket, actor: User, parent_id: int | None) -> None:
    """Make the ticket part of another (or of none). A ticket can't be its own ancestor."""
    if parent_id == ticket.parent_id:
        return
    parent = session.get(Ticket, parent_id) if parent_id is not None else None
    if parent_id is not None and parent is None:
        raise ServiceError("No such ticket")
    walk = parent
    while walk is not None:
        if walk.id == ticket.id:
            raise ServiceError("A ticket can't be part of itself or of one of its own child tickets")
        walk = walk.parent
    before = ticket.parent
    ticket.parent = parent
    session.flush()
    _event(session, ticket, EventType.parent_changed, actor, before=before.id if before else None, after=parent.id if parent else None)
    if before is not None:
        _event(session, before, EventType.child_changed, actor, child=ticket.id, subject=ticket.subject, action="removed")
    if parent is not None:
        _event(session, parent, EventType.child_changed, actor, child=ticket.id, subject=ticket.subject, action="added")


def update_ticket(session: Session, ticket: Ticket, actor: User, changes: dict) -> Ticket:
    """Apply agent edits. `changes` holds only the fields the caller sent. Resolving or closing
    needs the fields Settings > Ticket fields makes required (they can come in the same edit)."""
    _set_tracked_fields(session, ticket, actor, changes)
    if "computer_id" in changes:
        _set_computer(session, ticket, actor, changes.pop("computer_id"))
    if "parent_id" in changes:
        set_parent(session, ticket, actor, changes.pop("parent_id"))
    if "status" in changes:
        status = changes.pop("status")
        if status in sla.CLOSED_STATUSES and ticket.status not in sla.CLOSED_STATUSES:
            check_children(session, ticket)
            try:
                fields.check_closing(session, ticket)
            except fields.FieldError as exc:
                raise ServiceError(str(exc)) from None
        _set_status(session, ticket, status, actor)

    assessed = {f: changes.pop(f) for f in ("urgency", "impact") if f in changes}
    if assessed:
        old_assessment = {f: getattr(ticket, f) for f in assessed}
        for f, v in assessed.items():
            setattr(ticket, f, v)
        if any(old_assessment[f] != v for f, v in assessed.items()):
            _event(
                session,
                ticket,
                EventType.updated,
                actor,
                fields={f: {"before": getattr(old_assessment[f], "value", old_assessment[f]), "after": getattr(v, "value", v)} for f, v in assessed.items()},
            )
            # A new assessment re-prioritises, unless a priority was given explicitly.
            if "priority" not in changes and ticket.urgency and ticket.impact:
                changes["priority"] = priority_for(session, ticket.impact, ticket.urgency)

    if "priority" in changes:
        new = changes.pop("priority")
        if new != ticket.priority:
            old = ticket.priority
            _set_priority(session, ticket, new)
            _event(session, ticket, EventType.priority_changed, actor, before=old, after=new)

    edited = {}
    for field in ("subject", "description", "category"):
        if field in changes and changes[field] != getattr(ticket, field):
            edited[field] = {"before": getattr(ticket, field), "after": changes[field]}
            setattr(ticket, field, changes[field])
    if edited:
        _event(session, ticket, EventType.updated, actor, fields=edited)

    session.flush()
    return ticket


def add_comment(
    session: Session,
    ticket: Ticket,
    author: User,
    *,
    body: str,
    is_internal: bool,
    source: TicketSource,
    as_staff: bool | None = None,
    email_requester: bool = True,
) -> Comment:
    """as_staff: whether the author speaks as staff on this ticket. Defaults to their role; an
    agent on their own ticket in a queue they can't see (e.g. HR) is just the requester.
    email_requester: a staff reply is emailed as well as shown in the portal (False: portal only)."""
    is_agent = author.role in AGENT_ROLES if as_staff is None else as_staff
    if is_internal and not is_agent:
        raise ServiceError("Only staff working this ticket can add internal notes")

    comment = Comment(ticket_id=ticket.id, author=author, body=body, is_internal=is_internal, source=source)
    session.add(comment)
    session.flush()
    _event(session, ticket, EventType.comment_added, author, comment_id=comment.id, is_internal=is_internal)

    if is_agent and not is_internal:
        # An automated reply isn't a person responding, so it doesn't stop the SLA clock.
        if ticket.first_responded_at is None and author.email != AUTOMATION_EMAIL:
            ticket.first_responded_at = sla.utcnow()
        if ticket.status == TicketStatus.new:
            _set_status(session, ticket, TicketStatus.open, author)
        # An agent replying by email already addressed the requester themselves.
        if email_requester and source != TicketSource.email and author.id != ticket.requester_id:
            comment.emailed = outbox.agent_replied(session, ticket, comment)
    elif not is_agent and ticket.status in (TicketStatus.waiting_on_user, TicketStatus.resolved):
        # A requester reply puts the ticket back in front of the team.
        _set_status(session, ticket, TicketStatus.open, author)

    session.flush()
    return comment


def add_attachment(
    session: Session,
    ticket: Ticket,
    uploader: User,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    comment: Comment | None = None,
    as_staff: bool | None = None,
) -> Attachment:
    staff = uploader.role in AGENT_ROLES if as_staff is None else as_staff
    if comment is not None:
        if comment.ticket_id != ticket.id:
            raise ServiceError("Comment belongs to a different ticket")
        if not staff and comment.author_id != uploader.id:
            raise ServiceError("Requesters can only attach files to their own comments")
    stored = storage.save(data)
    attachment = Attachment(
        ticket_id=ticket.id,
        comment_id=comment.id if comment else None,
        uploaded_by_id=uploader.id,
        filename=(filename or "attachment")[:255],
        content_type=(content_type or "application/octet-stream")[:255],
        size_bytes=stored.size,
        sha256=stored.sha256,
        storage_key=stored.key,
    )
    session.add(attachment)
    session.flush()
    _event(
        session,
        ticket,
        EventType.attachment_added,
        uploader,
        attachment_id=str(attachment.id),
        filename=attachment.filename,
        size_bytes=attachment.size_bytes,
        is_internal=bool(comment and comment.is_internal),
    )
    return attachment


def change_requester(session: Session, ticket: Ticket, actor: User, requester: User) -> Ticket:
    """Link the ticket to the right person, e.g. when email came from an alias or a shared mailbox."""
    if not requester.is_active:
        raise ServiceError(f"{requester.email} is not an active user")
    if requester.id == ticket.requester_id:
        return ticket
    before = ticket.requester
    # Contact details that were the old requester's follow the new one; ones typed in stay.
    if ticket.contact_phone in (None, before.phone):
        ticket.contact_phone = requester.phone
    if ticket.department_id is None or (ticket.department is not None and ticket.department.label.lower() == (before.department or "").lower()):
        ticket.department = fields.department_for(session, requester.department)
    # The old requester's computer goes with them; the new one's comes in when it's certain.
    if ticket.computer is None or ticket.computer.assigned_user_id == before.id:
        ticket.computer = computer_for(session, requester)
    ticket.requester = requester
    session.flush()
    _event(session, ticket, EventType.requester_changed, actor, before=before.id, after=requester.id)
    return ticket


def requester_resolve(session: Session, ticket: Ticket, requester: User) -> Ticket:
    """The requester says it's fixed."""
    if ticket.status in sla.CLOSED_STATUSES:
        raise ServiceError("This ticket is already resolved or closed")
    check_children(session, ticket)
    if ticket.closure_reason_id is None:
        ticket.closure_reason = fields.requester_resolved(session)
    _set_status(session, ticket, TicketStatus.resolved, requester)
    session.flush()
    return ticket


MERGED = "merged"  # key of the closure reason merged tickets get


def merge(session: Session, source: Ticket, target: Ticket, actor: User, reason: FieldOption, note: str = "") -> Ticket:
    """Merge source into target: source's replies, notes and files move to target, its devices are
    linked there and its child tickets become target's; target gets a note with the reason; source
    is closed as merged and keeps its history. Email replies to source then thread onto target."""
    if source.id == target.id:
        raise ServiceError("A ticket can't be merged into itself")
    if source.merged_into_id is not None:
        raise ServiceError(f"#{source.id} was already merged into #{source.merged_into_id}")
    if target.merged_into_id is not None:
        raise ServiceError(f"#{target.id} was merged into #{target.merged_into_id}: merge into that one")
    walk = target.parent
    while walk is not None:
        if walk.id == source.id:
            raise ServiceError(f"#{target.id} is part of #{source.id}: merge the child ticket into its parent instead")
        walk = walk.parent
    if reason.list != FieldList.merge_reason or not reason.is_active:
        raise ServiceError("Pick why these tickets are being merged")

    moved_comments = session.execute(update(Comment).where(Comment.ticket_id == source.id).values(ticket_id=target.id)).rowcount
    moved_files = session.execute(update(Attachment).where(Attachment.ticket_id == source.id).values(ticket_id=target.id)).rowcount
    have = set(session.scalars(select(TicketDevice.device_id).where(TicketDevice.ticket_id == target.id)))
    for link in session.scalars(select(TicketDevice).where(TicketDevice.ticket_id == source.id)):
        if link.device_id not in have:
            session.add(TicketDevice(ticket_id=target.id, device_id=link.device_id, linked_by_id=actor.id))
    for child in session.scalars(select(Ticket).where(Ticket.parent_id == source.id)):
        child.parent_id = target.id
    if target.computer_id is None and source.computer_id is not None:
        target.computer_id = source.computer_id
    session.flush()
    session.expire(target)
    session.expire(source)

    lines = [f'Merged #{source.id} "{source.subject}" into this ticket. Reason: {reason.label}.']
    if note.strip():
        lines.append(note.strip())
    if source.requester_id != target.requester_id:
        lines.append(f"#{source.id} was raised by {source.requester.display_name} ({source.requester.email}).")
    lines.append(f"Moved here: {moved_comments} repl{'y' if moved_comments == 1 else 'ies'} and notes, {moved_files} file(s).")
    add_comment(session, target, actor, body="\n".join(lines), is_internal=True, source=TicketSource.agent, as_staff=True)
    _event(session, target, EventType.merged, actor, direction="in", other=source.id, reason=reason.label)

    source.merged_into_id = target.id
    closing = session.scalar(select(FieldOption).where(FieldOption.key == MERGED))
    if source.status not in sla.CLOSED_STATUSES:
        if closing is not None:
            source.closure_reason = closing
        _set_status(session, source, TicketStatus.closed, actor)
    add_comment(session, source, actor, body=f"Merged into #{target.id} \"{target.subject}\". Reason: {reason.label}.", is_internal=True, source=TicketSource.agent, as_staff=True)
    _event(session, source, EventType.merged, actor, direction="out", other=target.id, reason=reason.label)
    session.flush()
    return target
