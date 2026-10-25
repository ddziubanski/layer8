# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket text search: subject, description and notes, with PostgreSQL full-text search.

The "simple" configuration (no stemming, no stop words) suits names, hostnames, error codes and
old ticket numbers ("Track-It! ticket 17045") better than a language. The expressions below are
the ones the GIN indexes in migration c8e2f4a91d37 are built on; keep them identical so the
indexes are used.
"""
import re

from sqlalchemy import ColumnElement, func, literal, literal_column, select, union

from app.models import Comment, Ticket

CONFIG = literal_column("'simple'::regconfig")
TICKET_TEXT = func.to_tsvector(CONFIG, func.coalesce(Ticket.subject, literal_column("''")).op("||")(literal_column("' '")).op("||")(func.coalesce(Ticket.description, literal_column("''"))))
COMMENT_TEXT = func.to_tsvector(CONFIG, Comment.body)
MAX_LENGTH = 200


def matches(q: str) -> ColumnElement[bool] | None:
    """Tickets whose subject, description or any note matches every word of q (quotes for a phrase,
    "or", and -word to exclude, like a web search); a number also finds that ticket. None: nothing to search."""
    q = q.strip()[:MAX_LENGTH]
    if not re.search(r"\w", q):
        return None
    query = func.websearch_to_tsquery(CONFIG, q)
    # One list of ticket IDs from each index; an OR across them makes PostgreSQL read every ticket.
    ids = [select(Ticket.id).where(TICKET_TEXT.op("@@")(query)), select(Comment.ticket_id).where(COMMENT_TEXT.op("@@")(query))]
    number = q.lstrip("#")
    if number.isdigit() and len(number) < 18:
        ids.append(select(literal(int(number))))
    return Ticket.id.in_(union(*ids))
