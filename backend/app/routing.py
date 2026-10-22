"""Routing engine: picks a queue and priority for a new ticket.

Pure functions over plain data so the rules can be tested without a database.
Email and portal tickets go through exactly the same path.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.models import Priority, RoutingRule, TicketSource


@dataclass(frozen=True)
class TicketFacts:
    subject: str
    description: str
    category: str | None
    requester_email: str
    source: TicketSource
    priority: Priority
    # The queue the requester picked in the portal, if any.
    requested_queue_id: int | None = None


def rule_matches(rule: RoutingRule, facts: TicketFacts) -> bool:
    if rule.match_category is not None:
        if (facts.category or "").casefold() != rule.match_category.casefold():
            return False

    if rule.match_keywords:
        text = f"{facts.subject}\n{facts.description}".casefold()
        if not any(kw.casefold() in text for kw in rule.match_keywords if kw.strip()):
            return False

    if rule.match_requester is not None:
        pattern = rule.match_requester.casefold()
        email = facts.requester_email.casefold()
        if pattern.startswith("@"):
            if not email.endswith(pattern):
                return False
        elif email != pattern:
            return False

    if rule.match_source is not None and rule.match_source != facts.source:
        return False

    if rule.match_priority is not None and rule.match_priority != facts.priority:
        return False

    if rule.match_requested_queue_id is not None and rule.match_requested_queue_id != facts.requested_queue_id:
        return False

    return True


def first_match(rules: Iterable[RoutingRule], facts: TicketFacts) -> RoutingRule | None:
    """Return the first active rule, by position then id, whose conditions all match."""
    ordered = sorted((r for r in rules if r.is_active), key=lambda r: (r.position, r.id))
    for rule in ordered:
        if rule_matches(rule, facts):
            return rule
    return None
