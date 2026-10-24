# SPDX-License-Identifier: AGPL-3.0-or-later
"""Knowledge base: search, suggestions and similar tickets (Postgres full-text search, no extra
service), and drafting an article from a solved ticket.

Searching takes the words people typed and matches any of them (as word prefixes, so a request's
subject is matched while it's still being typed), ranked: an article whose title or keywords match
comes before one that only mentions it, and one in the request's category gets a lift.
"""

import re

from sqlalchemy import case, func, literal_column, select
from sqlalchemy.orm import Session

from app.models import Comment, KbArticle, KbAudience, KbStatus, Ticket, TicketStatus, User, UserRole

WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
MAX_WORDS = 16
SOLVED = (TicketStatus.resolved, TicketStatus.closed)
# A ticket must share this much with another to count as similar (ts_rank of an OR query).
SIMILAR_MIN_RANK = 0.02


def is_staff(user: User) -> bool:
    return user.role in (UserRole.agent, UserRole.admin) and not user.portal_only


def tsquery_text(text: str) -> str | None:
    """'Outlook keeps asking for my password' -> 'outlook:* | keeps:* | ...' (None: no words)."""
    words = list(dict.fromkeys(w.lower() for w in WORD.findall(text or "")))[:MAX_WORDS]
    return " | ".join(f"{w}:*" for w in words) or None


def _query(text: str):
    q = tsquery_text(text)
    return func.to_tsquery("english", q) if q else None


def articles(session: Session, user: User, q: str = "", *, category: str | None = None, status: KbStatus | None = None, limit: int = 20) -> list[KbArticle]:
    """Articles the user may read, best match first (newest first without words). Requesters see
    published ones for everyone; staff see drafts too unless a status is asked for."""
    stmt = select(KbArticle)
    if is_staff(user):
        stmt = stmt.where(KbArticle.status == status) if status else stmt.where(KbArticle.status != KbStatus.archived)
    else:
        stmt = stmt.where(KbArticle.status == KbStatus.published, KbArticle.audience == KbAudience.everyone)
    query = _query(q)
    if query is None:
        return list(session.scalars(stmt.order_by(KbArticle.updated_at.desc()).limit(limit)))
    rank = func.ts_rank(KbArticle.search, query)
    if category:
        rank = rank + case((func.lower(KbArticle.category) == category.lower(), 0.1), else_=0.0)
    return list(session.scalars(stmt.where(KbArticle.search.op("@@")(query)).order_by(rank.desc(), KbArticle.id).limit(limit)))


def similar_tickets(session: Session, ticket: Ticket, queue_ids, limit: int = 5) -> list[tuple[Ticket, float]]:
    """Solved tickets that read like this one (subject first), in queues the user may see."""
    query = _query(f"{ticket.subject} {' '.join(ticket.description.split()[:40])}")
    if query is None:
        return []
    weight = lambda w: literal_column(f"'{w}'")  # noqa: E731 (setweight takes a "char", not a bound varchar)
    doc = func.setweight(func.to_tsvector("english", Ticket.subject), weight("A")).op("||")(func.setweight(func.to_tsvector("english", func.left(Ticket.description, 4000)), weight("B")))
    rank = func.ts_rank(doc, query)
    stmt = select(Ticket, rank).where(Ticket.id != ticket.id, Ticket.status.in_(SOLVED), Ticket.merged_into_id.is_(None))
    if queue_ids is not None:
        stmt = stmt.where(Ticket.queue_id.in_(queue_ids))
    stmt = stmt.where(doc.op("@@")(query), rank >= SIMILAR_MIN_RANK).order_by(rank.desc(), Ticket.id.desc()).limit(limit)
    return [(t, float(r)) for t, r in session.execute(stmt)]


def draft_from_ticket(session: Session, ticket: Ticket, user: User) -> KbArticle:
    """A draft for review: the problem as asked, the fix as the last reply to the requester, and the
    technicians' notes to work from (staff only until someone publishes it)."""
    staff = [c for c in ticket_comments(session, ticket) if c.author_id != ticket.requester_id and c.author.role != UserRole.requester]
    replies = [c for c in staff if not c.is_internal]
    notes = [c for c in staff if c.is_internal]
    parts = ["## Problem", "", ticket.description.strip() or ticket.subject, "", "## Fix", "", replies[-1].body.strip() if replies else "(What fixed it.)"]
    if notes:
        parts += ["", "## Notes from the ticket", "", "Internal notes, to tidy up or remove before publishing:", ""]
        parts += [f"- {' '.join(n.body.split())[:500]}" for n in notes[-5:]]
    article = KbArticle(
        title=ticket.subject[:300], body="\n".join(parts), category=ticket.category, audience=KbAudience.staff, status=KbStatus.draft,
        source_ticket_id=ticket.id, author_id=user.id, updated_by_id=user.id,
    )
    session.add(article)
    session.flush()
    return article


def ticket_comments(session: Session, ticket: Ticket) -> list[Comment]:
    return list(session.scalars(select(Comment).where(Comment.ticket_id == ticket.id).order_by(Comment.id)))


def excerpt(body: str, length: int = 220) -> str:
    """The start of an article as plain text (Markdown marks dropped)."""
    text = re.sub(r"```.*?```", " ", body, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#+\s*|[*_`>]|^\s*[-+]\s+|^\s*\d+\.\s+", "", text, flags=re.M)
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1].rsplit(" ", 1)[0] + "…"


def visible(article: KbArticle, user: User) -> bool:
    return is_staff(user) or (article.status == KbStatus.published and article.audience == KbAudience.everyone)

