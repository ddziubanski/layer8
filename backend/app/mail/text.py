# SPDX-License-Identifier: AGPL-3.0-or-later
"""Subject and body handling for ticket email."""

import re
from datetime import datetime

from app import numbering

# Outgoing mail puts this above the quoted history; everything below it in a reply is dropped.
REPLY_MARKER = "##- Please type your reply above this line -##"

TAG_RE = re.compile(r"\[#(\d+)\]")
PREFIX_RE = re.compile(r"^\s*((re|fw|fwd|aw|wg|sv|vs|antw)\s*:\s*)+", re.IGNORECASE)

# Where a reply's quoted history starts, for clients that drop our marker.
QUOTE_STARTS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    # Outlook's separator line, then "From: Name <x@y>" with "Sent:" or "Date:" a line or two later.
    re.compile(r"^_{10,}\s*$", re.MULTILINE),
    re.compile(r"^From:\s.+\n(.*\n)?(Sent|Date):\s", re.IGNORECASE | re.MULTILINE),
    # Gmail, Apple Mail, Thunderbird: "On <date>, <name> wrote:" (may wrap onto two lines).
    re.compile(r"^On\s.+(\n.*)?wrote:\s*$", re.MULTILINE),
]


def ticket_tag(ticket_id: int, created_at: datetime | None = None) -> str:
    """The ticket's label in brackets, e.g. [#1042] or [HD-1042] (Settings > General)."""
    return f"[{numbering.label(ticket_id, created_at)}]"


def _label_tag() -> re.Pattern:
    return re.compile(r"\[" + numbering.pattern().pattern + r"\]")


def find_ticket_id(subject: str) -> int | None:
    """From the configured label in brackets, or the plain [#n] tag (older email, other formats)."""
    match = _label_tag().search(subject) or TAG_RE.search(subject)
    return int(match.group(1)) if match else None


def clean_subject(subject: str) -> str:
    """Subject for a new ticket: no ticket tags, no Re:/Fw: prefixes."""
    subject = _label_tag().sub("", subject)
    subject = TAG_RE.sub("", subject)
    subject = PREFIX_RE.sub("", subject).strip()
    return re.sub(r"\s{2,}", " ", subject) or "(no subject)"


def strip_quoted(body: str) -> str:
    """The new part of a reply, without the quoted history."""
    body = body.replace("\r\n", "\n")
    cut = len(body)

    marker = body.find(REPLY_MARKER)
    if marker != -1:
        cut = marker
    for pattern in QUOTE_STARTS:
        match = pattern.search(body, 0, cut)
        if match:
            cut = min(cut, match.start())

    lines = body[:cut].rstrip().split("\n")
    # Trailing "> quoted" lines from plain-text clients.
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    result = "\n".join(lines).strip()
    # If stripping removed everything, keep the original rather than lose the message.
    return result or body.strip()
