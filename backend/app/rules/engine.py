"""Running ticket rules.

- on_create / on_update: after a save commits, the tickets it touched are run
  through the matching rules in a fresh session. Changes rules make never
  trigger on_update rules (the session is flagged), so rules can't loop.
- schedule: the worker calls run_due(); each rule acts once per ticket until
  the ticket stops matching (RuleFire), then it can fire again.
- test: execute(..., dry_run=True) does everything inside a savepoint that is
  rolled back, and reports what would have changed.
"""

import logging
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timedelta

from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app import services, sla
from app.models import (
    Comment,
    Priority,
    RuleFire,
    RuleMode,
    RuleRun,
    RuleTrigger,
    Ticket,
    TicketEvent,
    TicketRule,
    TicketStatus,
    User,
)
from app.rules import actions, conditions, sqlmode
from app.services import ServiceError

log = logging.getLogger(__name__)

MAX_DETAILS = 50
SNAPSHOT = ["subject", "description", "category", "status", "priority", "queue_id", "assignee_id", "requester_id"]
IN_RULES = "in_rules"
PENDING = "rule_changes"


class RuleError(ValueError):
    pass


# Validation


def validate(rule: TicketRule) -> None:
    """Raise RuleError/ConditionError/ActionError/SqlRuleError with a message for the admin."""
    if rule.trigger == RuleTrigger.schedule and not (rule.schedule_minutes and rule.schedule_minutes >= 1):
        raise RuleError("Scheduled rules need schedule_minutes (1 or more)")
    if rule.mode == RuleMode.builder:
        conditions.validate(rule.conditions, allow_changes=rule.trigger == RuleTrigger.on_update)
        if not rule.conditions and rule.trigger != RuleTrigger.on_create:
            raise RuleError("Add at least one condition; only on_create rules may match every ticket")
        if rule.select_sql or rule.update_sql:
            raise RuleError("Builder rules can't have SQL; switch the rule to SQL mode")
    else:
        if not (rule.select_sql or "").strip():
            raise RuleError("SQL rules need a select query")
        if rule.conditions:
            raise RuleError("SQL rules select with SQL, not builder conditions")
        # Catch typos and permission problems now rather than at 3am. Nothing commits.
        sqlmode.select_ids(rule.select_sql, [])
        if rule.update_sql:
            sqlmode.propose_update(rule.update_sql, [], rule.max_rows)
    actions.validate(rule.actions)
    if not rule.actions and not rule.update_sql:
        raise RuleError("A rule needs at least one action or an update query")
    if rule.max_rows < 1:
        raise RuleError("max_rows must be at least 1")


# Matching


def match(session: Session, rule: TicketRule, now: datetime, limit_to: list[int] | None = None) -> list[int]:
    scope = [q.id for q in rule.queues]
    if rule.mode == RuleMode.builder:
        stmt = select(Ticket.id).where(*conditions.where(rule.conditions, now))
        if scope:
            stmt = stmt.where(Ticket.queue_id.in_(scope))
        if limit_to is not None:
            stmt = stmt.where(Ticket.id.in_(limit_to))
        return list(session.scalars(stmt.order_by(Ticket.id)))
    ids = sqlmode.select_ids(rule.select_sql, limit_to or [])
    if limit_to is not None:
        ids = [i for i in ids if i in set(limit_to)]
    if scope and ids:
        ids = list(session.scalars(select(Ticket.id).where(Ticket.id.in_(ids), Ticket.queue_id.in_(scope)).order_by(Ticket.id)))
    return ids


# Applying


def _snapshot(ticket: Ticket) -> dict:
    return {f: getattr(ticket, f) for f in SNAPSHOT}


def _diff(before: dict, after: dict) -> list[dict]:
    def show(v):
        return v.value if hasattr(v, "value") else v

    return [{"field": f, "before": show(before[f]), "after": show(after[f])} for f in SNAPSHOT if before[f] != after[f]]


def _apply_proposal(session: Session, ticket: Ticket, values: dict, actor: User, out: actions.Outcome) -> None:
    """Apply what a SQL update proposed, through the services."""
    edits = {}
    for f in ("subject", "description", "category"):
        if values[f] != getattr(ticket, f):
            edits[f] = values[f]
    if values["status"] != ticket.status.value:
        edits["status"] = TicketStatus(values["status"])
    if values["priority"] != ticket.priority.value:
        edits["priority"] = Priority(values["priority"])
    if edits:
        services.update_ticket(session, ticket, actor, edits)
    if values["queue_id"] != ticket.queue_id or values["assignee_id"] != ticket.assignee_id:
        moved = values["queue_id"] != ticket.queue_id
        services.assign(
            session,
            ticket,
            actor,
            queue_id=values["queue_id"] if moved else None,
            assignee_id=values["assignee_id"],
            unassign=values["assignee_id"] is None,
        )
        out.notes += moved
    if values["requester_id"] != ticket.requester_id:
        requester = session.get(User, values["requester_id"])
        if requester is None:
            raise ServiceError(f"User {values['requester_id']} doesn't exist")
        services.change_requester(session, ticket, actor, requester)


def execute(
    session: Session,
    rule: TicketRule,
    *,
    trigger: str,
    now: datetime,
    limit_to: list[int] | None = None,
    changed: set[str] | None = None,
    statuses: set[str] | None = None,
    dry_run: bool = False,
) -> RuleRun | None:
    """Run one rule. Returns the run record (not yet added to the session), or None when an
    on_update rule's change conditions don't apply to this save."""
    if changed is not None and not conditions.changes_match(rule.conditions, changed, statuses or set()):
        return None

    run = RuleRun(rule_id=rule.id, trigger=trigger, dry_run=dry_run, started_at=now, matched=0, changed=0, emails=0)
    details: list[dict] = []
    was_in_rules = session.info.get(IN_RULES)
    # Our own changes must not trigger on_update rules, real or test.
    session.info[IN_RULES] = True
    try:
        all_matched = match(session, rule, now, limit_to)
        ids = all_matched
        scheduled = rule.trigger == RuleTrigger.schedule
        if scheduled:
            fired = set(session.scalars(select(RuleFire.ticket_id).where(RuleFire.rule_id == rule.id)))
            ids = [i for i in all_matched if i not in fired]
        run.matched = len(ids)

        proposals = sqlmode.propose_update(rule.update_sql, ids, rule.max_rows) if rule.update_sql and ids else {}
        targets = set(ids) if rule.actions else set()
        touched = sorted(targets | set(proposals))
        if len(touched) > rule.max_rows:
            raise RuleError(f"This run would change {len(touched)} tickets, more than the rule's limit of {rule.max_rows}; nothing was changed")

        actor = services.automation_user(session)
        outer = session.begin_nested() if dry_run else None
        token = services.current_rule.set(rule.name)
        try:
            for tid in touched:
                ticket = session.get(Ticket, tid)
                if ticket is None:
                    continue
                before = _snapshot(ticket)
                out = actions.Outcome()
                step = session.begin_nested()
                try:
                    if tid in proposals:
                        _apply_proposal(session, ticket, proposals[tid], actor, out)
                    if tid in targets:
                        for action in rule.actions:
                            actions.apply(session, ticket, action, actor, out)
                    session.flush()
                    step.commit()
                except ServiceError as exc:
                    step.rollback()
                    details.append({"ticket_id": tid, "error": str(exc)})
                    continue
                change_list = _diff(before, _snapshot(ticket))
                if change_list or out.notes or out.emails:
                    run.changed += 1
                run.emails += out.emails
                details.append({"ticket_id": tid, "subject": ticket.subject, "changes": change_list, "notes": out.notes, "emails": out.emails, "skipped": out.skipped})
        finally:
            services.current_rule.reset(token)
            if outer is not None:
                outer.rollback()

        if scheduled and not dry_run:
            # Fire once per ticket; forget tickets that stopped matching so they can fire again.
            for tid in ids:
                session.add(RuleFire(rule_id=rule.id, ticket_id=tid, fired_at=now))
            session.execute(delete(RuleFire).where(RuleFire.rule_id == rule.id, RuleFire.ticket_id.not_in(all_matched or [0])))
    except (RuleError, conditions.ConditionError, actions.ActionError, sqlmode.SqlRuleError) as exc:
        run.error = str(exc)
    finally:
        session.info[IN_RULES] = was_in_rules
    run.details = details[:MAX_DETAILS]
    run.finished_at = sla.utcnow()
    return run


# Scheduled rules


def run_due(session_factory, now: datetime) -> int:
    """Run every scheduled rule that is due. Safe with several workers. Returns rules run."""
    with session_factory() as session:
        due = list(
            session.scalars(
                select(TicketRule.id).where(
                    TicketRule.is_active,
                    TicketRule.trigger == RuleTrigger.schedule,
                    (TicketRule.next_run_at.is_(None)) | (TicketRule.next_run_at <= now),
                ).order_by(TicketRule.position, TicketRule.id)
            )
        )
    ran = 0
    for rule_id in due:
        with session_factory() as session:
            rule = session.scalar(select(TicketRule).where(TicketRule.id == rule_id).with_for_update(skip_locked=True))
            if rule is None or (rule.next_run_at is not None and rule.next_run_at > now):
                continue
            try:
                run = execute(session, rule, trigger="schedule", now=now)
            except Exception as exc:  # never let one rule stop the others
                session.rollback()
                log.exception("Rule %s failed", rule_id)
                rule = session.get(TicketRule, rule_id)
                run = RuleRun(rule_id=rule_id, trigger="schedule", started_at=now, finished_at=sla.utcnow(), error=f"{type(exc).__name__}: {exc}")
            rule.last_run_at = now
            rule.next_run_at = now + timedelta(minutes=rule.schedule_minutes or 60)
            if run.matched or run.error:
                session.add(run)
            session.commit()
            ran += 1
    return ran


# on_create / on_update


def _collect(session: Session, _flush_context) -> None:
    if session.info.get(IN_RULES):
        return
    pending = session.info.setdefault(PENDING, defaultdict(list))
    for obj in session.new:
        if isinstance(obj, TicketEvent):
            pending[obj.ticket_id].append((obj.type.value if hasattr(obj.type, "value") else obj.type, dict(obj.data or {})))


def _after_commit(session: Session) -> None:
    pending = session.info.pop(PENDING, None)
    if pending and not session.info.get(IN_RULES):
        run_for_saves(session.get_bind(), dict(pending))


def _after_rollback(session: Session) -> None:
    session.info.pop(PENDING, None)


# Second, independent loop guard: never start rules from inside a rule run,
# even if a session somehow isn't flagged.
_running = ContextVar("rules_running", default=False)


def run_for_saves(bind, pending: dict[int, list[tuple[str, dict]]]) -> None:
    """Run on_create/on_update rules for the tickets a committed save touched."""
    if _running.get():
        log.error("Ticket rules tried to trigger themselves; skipped (loop guard)")
        return
    token = _running.set(True)
    try:
        with Session(bind=bind, expire_on_commit=False) as session:
            session.info[IN_RULES] = True
            rules = list(
                session.scalars(
                    select(TicketRule)
                    .where(TicketRule.is_active, TicketRule.trigger.in_([RuleTrigger.on_create, RuleTrigger.on_update]))
                    .order_by(TicketRule.position, TicketRule.id)
                )
            )
            if not rules:
                return
            now = sla.utcnow()
            for ticket_id, evs in pending.items():
                created = any(t == "created" for t, _ in evs)
                trigger = RuleTrigger.on_create if created else RuleTrigger.on_update
                roles = {}
                comment_ids = [d.get("comment_id") for t, d in evs if t == "comment_added"]
                if comment_ids:
                    roles = dict(session.execute(select(Comment.id, User.role).join(User, User.id == Comment.author_id).where(Comment.id.in_(comment_ids))).all())
                changed, statuses = conditions.describe_changes([(t, d, roles.get(d.get("comment_id"))) for t, d in evs])
                for rule in rules:
                    if rule.trigger != trigger:
                        continue
                    run = execute(
                        session,
                        rule,
                        trigger=trigger.value,
                        now=now,
                        limit_to=[ticket_id],
                        changed=changed if trigger == RuleTrigger.on_update else None,
                        statuses=statuses,
                    )
                    if run is not None and (run.matched or run.error):
                        session.add(run)
            session.commit()
    except Exception:
        # A broken rule must never break the save that triggered it; the save already committed.
        log.exception("Ticket rules failed after save")
    finally:
        _running.reset(token)


event.listen(Session, "after_flush", _collect)
event.listen(Session, "after_commit", _after_commit)
event.listen(Session, "after_rollback", _after_rollback)
