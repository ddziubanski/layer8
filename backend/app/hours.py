"""Service desk opening hours: when someone is in, and when someone is next expected.

Hours are kept as plain lines (Settings > Desk hours, and per queue in Settings > Queues), so they
are easy to edit and to import from an answers file:

    Mon-Fri 08:00-17:00
    Sat 09:00-12:00
    Tue 08:00-12:00, 13:00-17:00      (a lunch break)
    Sun 00:00-24:00                   (all day)

Days not named are closed; a later line replaces earlier hours for its days. Closed days are one per line, a date and an optional name:

    2026-12-25 Christmas Day

Times are the server's local time (the time zone chosen at install).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LOOK_AHEAD_DAYS = 31

Week = dict[int, list[tuple[int, int]]]  # weekday (0 = Monday) -> [(start, end)] in minutes from midnight


class HoursError(ValueError):
    pass


def _minutes(text: str, line: str) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m or int(m.group(2)) > 59 or int(m.group(1)) > 24 or (int(m.group(1)) == 24 and m.group(2) != "00"):
        raise HoursError(f"\"{line}\": {text!r} isn't a time like 08:00 (24-hour clock)")
    return int(m.group(1)) * 60 + int(m.group(2))


def _days(text: str, line: str) -> list[int]:
    out: list[int] = []
    for part in text.lower().split(","):
        ends = [p.strip()[:3] for p in part.split("-")]
        if len(ends) > 2 or any(e not in DAYS for e in ends):
            raise HoursError(f"\"{line}\": start with days like Mon-Fri, Sat or Mon,Wed")
        a, b = DAYS.index(ends[0]), DAYS.index(ends[-1])
        out += [d % 7 for d in range(a, b + 1 if b >= a else b + 8)]
    return out


def parse_week(text: str) -> Week:
    week: Week = {d: [] for d in range(7)}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z,\- ]+?)\s+(\d.*)", line)
        if not m:
            raise HoursError(f"\"{line}\": write days then times, e.g. Mon-Fri 08:00-17:00")
        spans = []
        for piece in m.group(2).split(","):
            ends = [p.strip() for p in piece.split("-")]
            if len(ends) != 2:
                raise HoursError(f"\"{line}\": times go from-to, e.g. 08:00-17:00")
            start, end = _minutes(ends[0], line), _minutes(ends[1], line)
            if end <= start:
                raise HoursError(f"\"{line}\": {ends[1]} isn't after {ends[0]}")
            spans.append((start, end))
        # A later line replaces earlier hours for its days (Mon-Fri ..., then Tue with a lunch break).
        for d in _days(m.group(1).replace(" ", ""), line):
            week[d] = sorted(spans)
    return week


def parse_closed(text: str) -> dict[date, str]:
    closed: dict[date, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*(.*)", line)
        try:
            day = date.fromisoformat(m.group(1)) if m else None
        except ValueError:
            day = None
        if day is None:
            raise HoursError(f"\"{line}\": start with a date like 2026-12-25")
        closed[day] = m.group(2).strip()
    return closed


def check_week(text: str) -> None:
    parse_week(text)


def check_closed(text: str) -> None:
    parse_closed(text)


@dataclass
class Status:
    open: bool
    # Open: when this stretch ends. Closed: when someone is next expected (None: not in the next month).
    closes_at: datetime | None
    next_open_at: datetime | None
    # Closed today for a named reason (a closed day), e.g. "Christmas Day".
    closed_for: str | None = None


def _at(day: date, minutes: int) -> datetime:
    # Local clock time on that day (a naive time is read as local, so DST changes come out right).
    return (datetime.combine(day, time.min) + timedelta(minutes=minutes)).astimezone()


def _stretches(week: Week, closed: dict[date, str], first: date, days: int) -> list[tuple[datetime, datetime]]:
    """Open stretches as real times, with touching ones joined (Mon 00:00-24:00 + Tue 00:00-08:00)."""
    spans = sorted(
        (_at(day, s), _at(day, e))
        for day in (first + timedelta(days=i) for i in range(days)) if day not in closed
        for s, e in week[day.weekday()]
    )
    merged: list[tuple[datetime, datetime]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def status(week_text: str, closed_text: str, now: datetime) -> Status:
    """Open or closed at `now`, using local time."""
    week, closed = parse_week(week_text), parse_closed(closed_text)
    local = now.astimezone()
    today = local.date()
    reason = closed.get(today) or None
    # From yesterday, for a stretch that runs past midnight into today.
    for start, end in _stretches(week, closed, today - timedelta(days=1), LOOK_AHEAD_DAYS + 2):
        if start <= local < end:
            return Status(open=True, closes_at=end, next_open_at=None)
        if start > local:
            return Status(open=False, closes_at=None, next_open_at=start, closed_for=reason)
    return Status(open=False, closes_at=None, next_open_at=None, closed_for=reason)


def when(at: datetime | None, now: datetime) -> str:
    """'today at 13:00', 'tomorrow at 08:00', 'on Monday at 08:00', 'on 2 January at 08:00'."""
    if at is None:
        return "soon"
    local, today = at.astimezone(), now.astimezone().date()
    clock = local.strftime("%H:%M")
    days = (local.date() - today).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {DAY_NAMES[local.weekday()]} at {clock}"
    return f"on {local.day} {local.strftime('%B')} at {clock}"


def lines(week_text: str) -> list[str]:
    """The hours as people read them: 'Mon-Fri 08:00-17:00'. Days with the same hours are joined."""
    week = parse_week(week_text)
    out: list[str] = []
    d = 0
    while d < 7:
        spans = week[d]
        e = d
        while e + 1 < 7 and week[e + 1] == spans:
            e += 1
        if spans:
            name = DAYS[d].title() if d == e else f"{DAYS[d].title()}-{DAYS[e].title()}"
            out.append(f"{name} " + ", ".join(f"{s // 60:02d}:{s % 60:02d}-{t // 60:02d}:{t % 60:02d}" for s, t in spans))
        d = e + 1
    return out
