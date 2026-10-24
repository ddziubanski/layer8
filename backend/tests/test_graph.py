# SPDX-License-Identifier: AGPL-3.0-or-later
"""GraphMailbox against a mocked Graph API (no tenant needed)."""

import json

import httpx
import pytest

from app.mail.graph import GraphError, GraphMailbox
from app.mail.types import Folder, OutgoingMessage

BASE = "https://graph.microsoft.com/v1.0/users/helpdesk@corp.local"

MESSAGE = {
    "id": "AAMk1",
    "internetMessageId": "<abc@corp.local>",
    "subject": "Printer jam",
    "from": {"emailAddress": {"name": "R. Alvarez", "address": "ralvarez@corp.local"}},
    "receivedDateTime": "2026-10-18T09:02:00Z",
    "internetMessageHeaders": [
        {"name": "In-Reply-To", "value": "<out1@corp.local>"},
        {"name": "Received", "value": "first"},
        {"name": "Received", "value": "second"},
    ],
    "body": {"contentType": "text", "content": "It jammed again."},
}


class FakeGraph:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list[httpx.Response]] = {}

    def on(self, method, path, *responses):
        self.routes[(method, BASE + path)] = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, str(request.url).split("?")[0])
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json={"error": {"code": "NotMocked", "message": str(key)}})
        return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def slept():
    return []


@pytest.fixture
def mailbox(graph, slept):
    client = httpx.Client(transport=httpx.MockTransport(graph))
    return GraphMailbox("helpdesk@corp.local", token=lambda: "tok", http=client, sleep=slept.append)


def test_fetch_parses_message_attachments_and_raw(graph, mailbox):
    graph.on("GET", "/mailFolders/inbox/messages", httpx.Response(200, json={"value": [MESSAGE]}))
    graph.on(
        "GET",
        "/messages/AAMk1/attachments",
        httpx.Response(
            200,
            json={
                "value": [
                    {"@odata.type": "#microsoft.graph.fileAttachment", "id": "at1", "name": "shot.png", "contentType": "image/png", "isInline": True},
                    {"@odata.type": "#microsoft.graph.itemAttachment", "id": "at2", "name": "forwarded mail"},
                ]
            },
        ),
    )
    graph.on("GET", "/messages/AAMk1/attachments/at1/$value", httpx.Response(200, content=b"PNGDATA"))
    graph.on("GET", "/messages/AAMk1/$value", httpx.Response(200, content=b"MIME"))

    [msg] = mailbox.fetch_inbox(25)
    assert msg.mailbox_id == "AAMk1"
    assert msg.internet_message_id == "<abc@corp.local>"
    assert (msg.from_address, msg.from_name) == ("ralvarez@corp.local", "R. Alvarez")
    assert msg.body_text == "It jammed again."
    assert msg.in_reply_to == ["<out1@corp.local>"]
    assert msg.headers["received"] == "first"
    assert [(a.filename, a.data, a.is_inline) for a in msg.attachments] == [("shot.png", b"PNGDATA", True)]
    assert msg.raw_mime == b"MIME"
    assert msg.load_error is None

    listing = graph.calls[0]
    assert listing.headers["Authorization"] == "Bearer tok"
    assert listing.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert listing.url.params["$orderby"] == "receivedDateTime asc"


def test_one_broken_message_does_not_block_the_batch(graph, mailbox):
    second = {**MESSAGE, "id": "AAMk2", "internetMessageId": "<two@corp.local>"}
    graph.on("GET", "/mailFolders/inbox/messages", httpx.Response(200, json={"value": [MESSAGE, second]}))
    graph.on("GET", "/messages/AAMk1/attachments", httpx.Response(500, text="boom"))
    graph.on("GET", "/messages/AAMk2/attachments", httpx.Response(200, json={"value": []}))
    graph.on("GET", "/messages/AAMk2/$value", httpx.Response(200, content=b"MIME"))
    first, ok = mailbox.fetch_inbox(25)
    assert "500" in first.load_error
    assert ok.load_error is None


def test_throttling_is_retried_with_retry_after(graph, mailbox, slept):
    graph.on(
        "GET",
        "/mailFolders/inbox/messages",
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"value": []}),
    )
    assert mailbox.fetch_inbox(25) == []
    assert slept == [7.0]


def test_errors_raise_after_retries(graph, mailbox, slept):
    graph.on("GET", "/mailFolders/inbox/messages", httpx.Response(503))
    with pytest.raises(GraphError, match="503"):
        mailbox.fetch_inbox(25)
    assert len(slept) == 5


def test_move_creates_folder_once(graph, mailbox):
    msg_a = GraphMailbox._parse(MESSAGE)
    graph.on("GET", "/mailFolders", httpx.Response(200, json={"value": []}))
    graph.on("POST", "/mailFolders", httpx.Response(201, json={"id": "F1"}))
    graph.on("POST", "/messages/AAMk1/move", httpx.Response(201, json={"id": "moved"}))
    mailbox.move(msg_a, Folder.processed)
    mailbox.move(msg_a, Folder.processed)

    calls = [(c.method, c.url.path.rsplit("/", 1)[-1]) for c in graph.calls]
    assert calls == [("GET", "mailFolders"), ("POST", "mailFolders"), ("POST", "move"), ("POST", "move")]
    assert json.loads(graph.calls[1].content) == {"displayName": "Processed"}
    assert json.loads(graph.calls[2].content) == {"destinationId": "F1"}


def test_send_creates_draft_then_sends_and_returns_message_id(graph, mailbox):
    graph.on("POST", "/messages", httpx.Response(201, json={"id": "D1", "internetMessageId": "<d1@corp.local>"}))
    graph.on("POST", "/messages/D1/send", httpx.Response(202))
    mid = mailbox.send(OutgoingMessage("ralvarez@corp.local", "[#1000] Printer jam", "body", {"X-Helpdesk-Ticket": "1000"}))
    assert mid == "<d1@corp.local>"
    draft = json.loads(graph.calls[0].content)
    assert draft["toRecipients"] == [{"emailAddress": {"address": "ralvarez@corp.local"}}]
    assert draft["internetMessageHeaders"] == [{"name": "X-Helpdesk-Ticket", "value": "1000"}]
    assert draft["body"] == {"contentType": "Text", "content": "body"}


def test_recipients_are_read_for_queue_aliases():
    item = {**MESSAGE, "toRecipients": [{"emailAddress": {"address": "HR@corp.com"}}], "ccRecipients": [{"emailAddress": {"address": "helpdesk@corp.local"}}, {"emailAddress": {}}]}
    assert GraphMailbox._parse(item).to_addresses == ["hr@corp.com", "helpdesk@corp.local"]
