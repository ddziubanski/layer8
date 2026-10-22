"""Ticket numbers and labels (Settings > General).

The number is the ticket's id: a plain, unique, increasing integer. Admins choose where it
starts (only ever moved forward) and how it's shown, e.g. "#{n}", "HD-{n}", "INC{n:06}" or
"{yyyy}-{n}". The label is display only: email subjects carry it in brackets, and threading
recognises the configured label as well as the plain [#n] tag, so changing the format later
doesn't break replies to old email.
"""

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

TOKEN = re.compile(r"\{(n(?::0?(\d{1,2}))?|yyyy|yy)\}")
LITERAL = re.compile(r"[A-Za-z0-9 #_./-]*")


class NumberingError(ValueError):
    pass


def check_format(fmt: str) -> None:
    """A usable label format: one {n}, other text plain."""
    if len(fmt) > 30:
        raise NumberingError("At most 30 characters")
    numbers = [m for m in TOKEN.finditer(fmt) if m.group(1).startswith("n")]
    if len(numbers) != 1:
        raise NumberingError("Put the number in once: {n}, or {n:06} for six digits with leading zeros")
    leftover = TOKEN.sub("", fmt)
    if "{" in leftover or "}" in leftover:
        raise NumberingError("Only {n}, {n:06}, {yyyy} and {yy} can go in braces")
    if not LITERAL.fullmatch(leftover):
        raise NumberingError("Use letters, digits, spaces and # _ . / - around the number")


def label(number: int, created_at: datetime | None = None, fmt: str | None = None) -> str:
    fmt = fmt or settings.ticket_label_format or "#{n}"
    year = (created_at or datetime.now()).astimezone().year

    def part(m: re.Match) -> str:
        token = m.group(1)
        if token == "yyyy":
            return f"{year:04d}"
        if token == "yy":
            return f"{year % 100:02d}"
        width = int(m.group(2) or 0)
        return f"{number:0{width}d}" if width else str(number)

    return TOKEN.sub(part, fmt)


def pattern(fmt: str | None = None) -> re.Pattern:
    """The label as a regex with the number captured, e.g. HD-(\\d+)."""
    fmt = fmt or settings.ticket_label_format or "#{n}"
    out, pos = [], 0
    for m in TOKEN.finditer(fmt):
        out.append(re.escape(fmt[pos : m.start()]))
        token = m.group(1)
        out.append(r"\d{4}" if token == "yyyy" else r"\d{2}" if token == "yy" else r"(\d+)")
        pos = m.end()
    out.append(re.escape(fmt[pos:]))
    return re.compile("".join(out))


def apply_first_number(session: Session, table: str = "tickets", first: int | None = None) -> int:
    """Move a numbering sequence (tickets, or purchase_orders) so the next one gets at least `first`
    (default: ticket_first_number), never back past rows that exist. Returns the next number."""
    assert table in ("tickets", "purchase_orders")
    first = settings.ticket_first_number if first is None else first
    seq = session.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}).scalar()
    highest = session.execute(text(f"SELECT coalesce(max(id), 0) FROM {table}")).scalar()
    last, called = session.execute(text(f"SELECT last_value, is_called FROM {seq}")).one()
    current_next = last + 1 if called else last
    # Before the first ticket it can go anywhere; after, only forward.
    nxt = first if highest == 0 else max(first, highest + 1, current_next)
    session.execute(text("SELECT setval(:seq, :n, false)"), {"seq": seq, "n": nxt})
    return nxt
