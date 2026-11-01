# SPDX-License-Identifier: AGPL-3.0-or-later
"""Suggest problems from patterns: when enough open tickets arrive close together and share
something (a device model, a system, a switch, a subnet, a site, or a distinctive word in the
subject), the Problems page offers "These look like one problem" with the evidence. It's only a
suggestion: one click creates a problem with those tickets linked, and it can be dismissed.

Settings > Problems: problem_suggest_tickets (0: off) and problem_suggest_hours.
Only tickets the technician can see count, and tickets already in a problem are left out.
"""
import hashlib
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import sla
from app.api.tickets import visible
from app.changes import _ticket_footprint
from app.config import settings
from app.models import Device, ProblemSuggestionDismissal, Ticket, User, problem_tickets

# Words too common in ticket subjects to mean anything on their own.
STOP = set("""about after again all also and any are back been before being but can cannot cant could does doesnt dont down each
error errors from have help having into issue issues just keeps like more need needs not now off only other please problem problems
really request since some still than that the their them then there these they this those today unable very want wants was were what
when where which while will with without wont work working works would your""".split())


def _words(subject: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", subject.lower()) if w not in STOP and not w.isdigit()}


def _key(ids) -> str:
    """The exact tickets: dismissing them hides that group under any reason, until a ticket joins."""
    return "tickets:" + hashlib.sha256(",".join(map(str, sorted(ids))).encode()).hexdigest()[:24]


def suggestions(session, user: User) -> list[dict]:
    need = settings.problem_suggest_tickets
    if not need:
        return []
    since = sla.utcnow() - timedelta(hours=settings.problem_suggest_hours)
    in_problem = select(problem_tickets.c.ticket_id)
    stmt = (visible(select(Ticket), user)
            .where(Ticket.created_at >= since, Ticket.status.not_in(sla.CLOSED_STATUSES), Ticket.merged_into_id.is_(None), Ticket.id.not_in(in_problem))
            .options(selectinload(Ticket.computer)).order_by(Ticket.created_at).limit(500))
    tickets = session.scalars(stmt).all()
    if len(tickets) < need:
        return []
    groups: dict[tuple[str, str], tuple[str, set[int]]] = {}

    def add(kind: str, value: str, label: str, tid: int):
        if value:
            groups.setdefault((kind, value.lower()), (label, set()))[1].add(tid)

    models: dict[int, str] = dict(session.execute(select(Device.id, Device.model).where(Device.model.is_not(None), Device.model != "")).all())
    for t in tickets:
        fp = _ticket_footprint(session, t)
        for did in fp.device_names:
            if models.get(did):
                add("model", models[did], f"Device model {models[did]}", t.id)
        for name in fp.system_names.values():
            add("system", name, f"System {name}", t.id)
        for low, name in fp.switches.items():
            add("switch", low, f"Switch {name}", t.id)
        for label in fp.subnets.values():
            add("subnet", label, f"Subnet {label}", t.id)
        for low, name in fp.sites.items():
            add("site", low, f"Site {name}", t.id)
        for w in _words(t.subject):
            add("words", w, f"“{w}” in the subject", t.id)

    dismissed = set(session.scalars(select(ProblemSuggestionDismissal.key)))
    by_id = {t.id: t for t in tickets}
    rank = {"model": 0, "system": 0, "switch": 1, "subnet": 2, "site": 3, "words": 4}
    out, seen_sets = [], []
    for (kind, value), (label, ids) in sorted(groups.items(), key=lambda kv: (-len(kv[1][1]), rank[kv[0][0]])):
        if len(ids) < need:
            continue
        # A cluster already offered under a stronger reason isn't offered again.
        if any(ids <= s for s in seen_sets):
            continue
        key = _key(ids)
        if key in dismissed:
            continue
        seen_sets.append(ids)
        rows = sorted((by_id[i] for i in ids), key=lambda t: t.created_at)
        out.append({
            "key": key,
            "kind": kind,
            "evidence": f"{len(rows)} tickets in {settings.problem_suggest_hours} hours share {label}",
            "suggested_title": rows[0].subject[:300] if kind == "words" else f"{label}: {rows[0].subject}"[:300],
            "tickets": rows,
        })
        if len(out) == 10:
            break
    return out


def dismiss(session, key: str, user: User) -> None:
    if session.get(ProblemSuggestionDismissal, key) is None:
        session.add(ProblemSuggestionDismissal(key=key[:200], dismissed_by_id=user.id))
