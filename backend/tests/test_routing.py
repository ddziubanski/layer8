"""Routing engine rules, without a database."""

from app.models import Priority, RoutingRule, TicketSource
from app.routing import TicketFacts, first_match, rule_matches


def facts(**kw) -> TicketFacts:
    base = dict(
        subject="Switch port flapping, 2nd floor",
        description="Desks keep losing connection",
        category=None,
        requester_email="ralvarez@corp.local",
        source=TicketSource.email,
        priority=Priority.medium,
    )
    return TicketFacts(**(base | kw))


def rule(id=1, position=100, is_active=True, **kw) -> RoutingRule:
    return RoutingRule(id=id, name=f"rule {id}", position=position, is_active=is_active, **kw)


def test_empty_rule_matches_everything():
    assert rule_matches(rule(), facts())


def test_keywords_any_match_case_insensitive_in_subject_or_description():
    assert rule_matches(rule(match_keywords=["printer", "SWITCH"]), facts())
    assert rule_matches(rule(match_keywords=["connection"]), facts())
    assert not rule_matches(rule(match_keywords=["printer", "vpn"]), facts())


def test_blank_keywords_do_not_match_everything():
    assert not rule_matches(rule(match_keywords=["", "  "]), facts())


def test_category_is_case_insensitive_and_requires_a_category():
    assert rule_matches(rule(match_category="network"), facts(category="Network"))
    assert not rule_matches(rule(match_category="network"), facts(category=None))


def test_requester_exact_or_domain():
    assert rule_matches(rule(match_requester="RAlvarez@corp.local"), facts())
    assert rule_matches(rule(match_requester="@corp.local"), facts())
    assert not rule_matches(rule(match_requester="@hr.corp.local"), facts())
    # A domain pattern must not match a lookalike domain.
    assert not rule_matches(rule(match_requester="@corp.local"), facts(requester_email="x@evilcorp.local.com"))


def test_source_and_priority():
    assert rule_matches(rule(match_source=TicketSource.email), facts())
    assert not rule_matches(rule(match_source=TicketSource.portal), facts())
    assert rule_matches(rule(match_priority=Priority.medium), facts())
    assert not rule_matches(rule(match_priority=Priority.high), facts())


def test_all_conditions_must_match():
    r = rule(match_keywords=["switch"], match_source=TicketSource.portal)
    assert not rule_matches(r, facts(source=TicketSource.email))
    assert rule_matches(r, facts(source=TicketSource.portal))


def test_first_match_uses_position_then_id_and_skips_inactive():
    rules = [
        rule(id=3, position=10, is_active=False, match_keywords=["switch"]),
        rule(id=2, position=20, match_keywords=["switch"]),
        rule(id=1, position=20, match_keywords=["switch"]),
        rule(id=4, position=5, match_keywords=["vpn"]),
    ]
    assert first_match(rules, facts()).id == 1


def test_no_match_returns_none():
    assert first_match([rule(match_keywords=["vpn"])], facts()) is None
