# SPDX-License-Identifier: AGPL-3.0-or-later
"""What intake and the outbox need from a mailbox. Graph implements it; tests use a fake."""

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class Folder(enum.StrEnum):
    processed = "Processed"
    rejected = "Rejected"
    failed = "Failed"


@dataclass
class InboundAttachment:
    filename: str
    content_type: str
    data: bytes
    is_inline: bool = False


@dataclass
class InboundMessage:
    mailbox_id: str  # the mail system's own ID, used to move the message
    internet_message_id: str
    from_address: str
    from_name: str
    subject: str
    body_text: str
    received_at: datetime | None = None
    # Who it was sent to (To and Cc), lower-case: which mailbox alias it came in on.
    to_addresses: list[str] = field(default_factory=list)
    # Header names are lower-cased.
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[InboundAttachment] = field(default_factory=list)
    raw_mime: bytes | None = None
    # Set when the message was listed but its details couldn't be fetched;
    # intake files it under Failed instead of blocking the rest of the inbox.
    load_error: str | None = None

    @property
    def in_reply_to(self) -> list[str]:
        ids = f"{self.headers.get('in-reply-to', '')} {self.headers.get('references', '')}"
        return [part for part in ids.split() if part.startswith("<") and part.endswith(">")]


@dataclass
class OutgoingAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class OutgoingMessage:
    to_address: str
    subject: str
    body: str
    # Extra headers; Graph only allows names starting with "X-".
    headers: dict[str, str] = field(default_factory=dict)
    # Small files only (Graph takes up to about 3 MB inline), e.g. a report PDF.
    attachments: list[OutgoingAttachment] = field(default_factory=list)


class Mailbox(Protocol):
    address: str

    def fetch_inbox(self, limit: int) -> list[InboundMessage]:
        """Oldest messages still in the inbox. Processed ones are moved out, so the inbox is the backlog."""

    def move(self, message: InboundMessage, folder: Folder) -> None: ...

    def send(self, message: OutgoingMessage) -> str:
        """Send and return the Message-ID the mail system assigned."""
