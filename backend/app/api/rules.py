"""Custom ticket rules (KACE-style). Admins only: SQL rules can read every ticket."""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app import permissions, sla
from app.api.admin import get_or_404, load_queues
from app.auth import SessionDep
from app.config import settings
from app.models import RuleRun, RuleTrigger, TicketRule
from app.rules import actions, conditions, engine, sqlmode
from app.schemas import RuleCatalogOut, RuleRunOut, TicketRuleIn, TicketRuleOut

router = APIRouter(prefix="/api/ticket-rules", tags=["ticket rules"])
RulesManager = permissions.require("rules_manage")

RULE_ERRORS = (engine.RuleError, conditions.ConditionError, actions.ActionError, sqlmode.SqlRuleError)


def _apply(session, rule: TicketRule, body: TicketRuleIn) -> None:
    data = body.model_dump(exclude={"queue_ids"})
    for field, value in data.items():
        setattr(rule, field, value)
    for f in ("select_sql", "update_sql"):
        if not (getattr(rule, f) or "").strip():
            setattr(rule, f, None)
    if rule.trigger != RuleTrigger.schedule:
        rule.schedule_minutes = None
        rule.next_run_at = None
    rule.queues = load_queues(session, body.queue_ids)
    try:
        engine.validate(rule)
    except RULE_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/catalog", response_model=RuleCatalogOut)
def catalog(_: RulesManager):
    return RuleCatalogOut(
        ticket_fields=conditions.TICKET_FIELDS,
        change_fields=conditions.CHANGE_FIELDS,
        actions=actions.ACTIONS,
        template_vars=actions.TEMPLATE_VARS,
        sql_enabled=bool(settings.rules_database_url),
    )


@router.get("", response_model=list[TicketRuleOut])
def list_rules(_: RulesManager, session: SessionDep):
    return session.scalars(select(TicketRule).order_by(TicketRule.position, TicketRule.id)).all()


@router.post("", response_model=TicketRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: TicketRuleIn, _: RulesManager, session: SessionDep):
    rule = TicketRule()
    session.add(rule)
    _apply(session, rule, body)
    session.commit()
    return rule


@router.get("/{rule_id}", response_model=TicketRuleOut)
def get_rule(rule_id: int, _: RulesManager, session: SessionDep):
    return get_or_404(session, TicketRule, rule_id)


@router.put("/{rule_id}", response_model=TicketRuleOut)
def replace_rule(rule_id: int, body: TicketRuleIn, _: RulesManager, session: SessionDep):
    rule = get_or_404(session, TicketRule, rule_id)
    _apply(session, rule, body)
    session.commit()
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, _: RulesManager, session: SessionDep):
    session.delete(get_or_404(session, TicketRule, rule_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{rule_id}/test", response_model=RuleRunOut)
def test_rule(rule_id: int, _: RulesManager, session: SessionDep, ticket_id: int | None = Query(default=None)):
    """Run the rule for real inside a transaction that is rolled back: shows what would match and change.

    For on_create/on_update rules, pass ticket_id to test against one ticket (change conditions are ignored).
    """
    rule = get_or_404(session, TicketRule, rule_id)
    run = engine.execute(session, rule, trigger="test", now=sla.utcnow(), limit_to=[ticket_id] if ticket_id else None, dry_run=True)
    session.add(run)
    session.commit()
    return run


@router.post("/{rule_id}/run", response_model=RuleRunOut)
def run_rule(rule_id: int, _: RulesManager, session: SessionDep):
    """Run a scheduled rule now, for real."""
    rule = get_or_404(session, TicketRule, rule_id)
    if rule.trigger != RuleTrigger.schedule:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only scheduled rules can be run by hand; test the others")
    now = sla.utcnow()
    run = engine.execute(session, rule, trigger="manual", now=now)
    rule.last_run_at = now
    session.add(run)
    session.commit()
    return run


@router.get("/{rule_id}/runs", response_model=list[RuleRunOut])
def rule_runs(rule_id: int, _: RulesManager, session: SessionDep, limit: int = Query(default=50, ge=1, le=500)):
    get_or_404(session, TicketRule, rule_id)
    return session.scalars(select(RuleRun).where(RuleRun.rule_id == rule_id).order_by(RuleRun.id.desc()).limit(limit)).all()
