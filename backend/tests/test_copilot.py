import json

import httpx
import pytest

from app import copilot, teams_chat
from app.config import settings
from app.models import Comment, EventType, TicketEvent
from app import services
from app.models import TicketSource
from tests.conftest import as_user


@pytest.fixture
def ticket(db, requester, service_desk):
    t = services.create_ticket(db, subject="VPN down", description="Since this morning.", category=None, requester=requester, source=TicketSource.portal, actor=requester)
    db.commit()
    return t


def graph(record: list):
    def handle(request: httpx.Request):
        record.append((request.url.path, json.loads(request.content or b"{}")))
        if request.url.path == "/beta/copilot/conversations":
            return httpx.Response(201, json={"id": "conv-1"})
        return httpx.Response(200, json={"id": "conv-1", "messages": [
            {"text": "How do I reset the VPN?"},
            {"text": "Reset the **VPN** profile.", "attributions": [
                {"attributionType": "citation", "providerDisplayName": "VPN guide", "seeMoreWebUrl": "https://contoso.sharepoint.com/vpn.docx"},
                {"attributionType": "annotation", "seeMoreWebUrl": "https://example.com/ignored"},
                {"attributionType": "citation", "providerDisplayName": "Dup", "seeMoreWebUrl": "https://contoso.sharepoint.com/vpn.docx"},
            ]},
        ]})
    return handle


def test_ask_starts_a_conversation_sends_context_and_keeps_citations():
    seen = []
    with httpx.Client(transport=httpx.MockTransport(graph(seen))) as http:
        out = copilot.ask("token", "How do I reset the VPN?", ["Ticket 1001: VPN down"], http=http)
    assert out == {"conversation_id": "conv-1", "answer": "Reset the **VPN** profile.", "citations": [{"title": "VPN guide", "url": "https://contoso.sharepoint.com/vpn.docx"}]}
    assert [p for p, _ in seen] == ["/beta/copilot/conversations", "/beta/copilot/conversations/conv-1/chat"]
    body = seen[1][1]
    assert body["message"] == {"text": "How do I reset the VPN?"} and body["additionalContext"] == [{"text": "Ticket 1001: VPN down"}]
    assert body["locationHint"]["timeZone"]
    # A running conversation goes straight to chat.
    seen.clear()
    with httpx.Client(transport=httpx.MockTransport(graph(seen))) as http:
        copilot.ask("token", "And then?", [], "conv-1", http=http)
    assert [p for p, _ in seen] == ["/beta/copilot/conversations/conv-1/chat"] and "additionalContext" not in seen[0][1]


def test_refusal_explains_licence_and_consent():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as http, pytest.raises(copilot.CopilotError, match="licence"):
        copilot.ask("token", "Hi", [], http=http)


def test_ticket_context_is_capped_and_labelled(db, ticket):
    comments = [Comment(ticket_id=ticket.id, author=ticket.requester, body="x" * 5000, is_internal=i % 2 == 0) for i in range(12)]
    for i, c in enumerate(comments):
        c.created_at = ticket.created_at
    parts = copilot.ticket_context(ticket, comments)
    assert parts[0].startswith(f"Help desk ticket {ticket.id}")
    assert sum(len(p) for p in parts) <= copilot.MAX_CONTEXT
    assert any(p.startswith("Internal note by") for p in parts[1:]) or len(parts) < 3


def test_endpoint_needs_copilot_on_and_logs_the_question(client, db, agent, ticket, monkeypatch):
    assert client.post(f"/api/tickets/{ticket.id}/copilot", json={"question": "Hi"}, headers=as_user(agent)).status_code == 409
    monkeypatch.setattr(settings, "copilot_enabled", True)
    monkeypatch.setattr(teams_chat, "access", lambda uid, wanted=None: ("token", {}))
    asked = {}
    def fake(token, question, context, conversation_id=None):
        asked.update(question=question, context=context, conversation_id=conversation_id)
        return {"conversation_id": "conv-9", "answer": "Try this.", "citations": []}
    monkeypatch.setattr(copilot, "ask", fake)
    r = client.post(f"/api/tickets/{ticket.id}/copilot", json={"question": " What now? ", "include_ticket": False}, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "Try this." and asked["question"] == "What now?" and asked["context"] == []
    events = db.query(TicketEvent).filter_by(ticket_id=ticket.id, type=EventType.updated).all()
    assert any(e.data.get("copilot") == "asked" and e.data.get("shared_ticket") is False for e in events)


def test_not_connected_says_reconnect(client, agent, ticket, monkeypatch):
    monkeypatch.setattr(settings, "copilot_enabled", True)
    def refuse(uid, wanted=None):
        raise teams_chat.ConnectionError("no")
    monkeypatch.setattr(teams_chat, "access", refuse)
    r = client.post(f"/api/tickets/{ticket.id}/copilot", json={"question": "Hi"}, headers=as_user(agent))
    assert r.status_code == 409 and "My preferences" in r.json()["detail"]
