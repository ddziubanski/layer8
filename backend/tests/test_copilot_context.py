import httpx
import pytest
from alembic import command
from sqlalchemy import inspect, select

from app import copilot, services, teams_chat
from app.config import settings
from app.models import CopilotConversation, Queue, TicketEvent, TicketSource
from tests.conftest import alembic_config, as_user


@pytest.fixture
def ticket(db, requester, service_desk):
    row = services.create_ticket(db, subject="Synthetic VPN request", description="Please help reconnect.",
        category=None, requester=requester, source=TicketSource.portal, actor=requester)
    db.commit()
    return row


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, "copilot_enabled", True)
    account = {"oid": "technician-1", "config": {"tenant": "tenant-1", "client": "client-1"}}
    monkeypatch.setattr(teams_chat, "access", lambda *a: ("synthetic-token", account))
    calls = []
    def ask(token, question, context, conversation_id=None):
        calls.append({"question": question, "context": context, "conversation_id": conversation_id})
        return {"conversation_id": conversation_id or f"conversation-{len(calls)}", "answer": "Try reconnecting.", "citations": []}
    monkeypatch.setattr(copilot, "ask", ask)
    return account, calls


def test_preview_filters_internal_notes_and_never_calls_microsoft(client, db, agent, ticket, provider, monkeypatch):
    monkeypatch.setattr(teams_chat, "access", lambda *a: pytest.fail("preview must stay local"))
    services.add_comment(db, ticket, agent, body="Private investigation", is_internal=True, source=TicketSource.agent)
    services.add_comment(db, ticket, agent, body="Public progress", is_internal=False, source=TicketSource.agent, email_requester=False)
    db.commit()
    url = f"/api/tickets/{ticket.id}/copilot/context"
    plain = client.get(url, headers=as_user(agent))
    assert plain.status_code == 200, plain.text
    assert "Public progress" in plain.text and "Private investigation" not in plain.text
    assert plain.json()["characters"] <= plain.json()["limit"] == 12000
    assert plain.json()["digest"] == copilot.context_digest(plain.json()["sections"])
    private = client.get(url + "?include_internal=true", headers=as_user(agent))
    assert "Private investigation" in private.text
    assert private.json()["digest"] != plain.json()["digest"]


def test_stale_preview_is_rejected_before_token_or_provider_access(client, db, agent, ticket, provider, monkeypatch):
    preview = client.get(f"/api/tickets/{ticket.id}/copilot/context", headers=as_user(agent)).json()
    ticket.description = "New information after review"; db.commit()
    monkeypatch.setattr(teams_chat, "access", lambda *a: pytest.fail("do not acquire a token"))
    response = client.post(f"/api/tickets/{ticket.id}/copilot", headers=as_user(agent),
        json={"question": "What next?", "context_digest": preview["digest"]})
    assert response.status_code == 409 and "changed" in response.json()["detail"]
    assert not provider[1]


def test_reviewed_context_matches_dispatch_and_history(client, db, agent, ticket, provider):
    services.add_comment(db, ticket, agent, body="Internal detail", is_internal=True, source=TicketSource.agent); db.commit()
    preview = client.get(f"/api/tickets/{ticket.id}/copilot/context?include_internal=true", headers=as_user(agent)).json()
    response = client.post(f"/api/tickets/{ticket.id}/copilot", headers=as_user(agent),
        json={"question": "  Summarize  ", "include_internal": True, "context_digest": preview["digest"]})
    assert response.status_code == 200, response.text
    assert provider[1][0]["context"] == preview["sections"]
    assert provider[1][0]["question"] == "Summarize"
    event = db.scalar(select(TicketEvent).where(TicketEvent.data["copilot"].as_string() == "asked"))
    assert event.data["shared_internal"] and event.data["context_digest"] == preview["digest"]


def test_conversation_resumes_only_on_original_ticket_user_and_account(client, db, agent, admin, ticket, requester, provider):
    url = f"/api/tickets/{ticket.id}/copilot"
    first = client.post(url, headers=as_user(agent), json={"question": "Help", "include_ticket": False})
    assert first.status_code == 200, first.text
    body = {"question": "Continue", "include_ticket": False, "conversation_id": first.json()["conversation_id"]}
    assert client.post(url, headers=as_user(agent), json=body).status_code == 200
    assert provider[1][-1]["conversation_id"] == first.json()["conversation_id"]
    assert len(list(db.scalars(select(CopilotConversation)))) == 1
    assert client.post(url, headers=as_user(admin), json=body).status_code == 409
    other = services.create_ticket(db, subject="Other ticket", description="Other context", category=None,
        requester=requester, source=TicketSource.portal, actor=requester); db.commit()
    assert client.post(f"/api/tickets/{other.id}/copilot", headers=as_user(agent), json=body).status_code == 409
    provider[0]["oid"] = "another-microsoft-user"
    assert client.post(url, headers=as_user(agent), json=body).status_code == 409
    assert len(provider[1]) == 2


def test_changing_sharing_requires_a_new_conversation(client, agent, ticket, provider):
    url = f"/api/tickets/{ticket.id}/copilot"
    first = client.post(url, headers=as_user(agent), json={"question": "Help", "include_internal": True}).json()
    for sharing in ({"include_ticket": False}, {"include_internal": False}):
        response = client.post(url, headers=as_user(agent), json={"question": "Continue", "conversation_id": first["conversation_id"], **sharing})
        assert response.status_code == 409
    assert len(provider[1]) == 1


def test_requesters_hidden_queues_and_disabled_preview_cannot_share_context(client, db, agent, requester, ticket, provider, monkeypatch):
    url = f"/api/tickets/{ticket.id}/copilot"
    assert client.get(url + "/context", headers=as_user(requester)).status_code == 403
    assert client.post(url, json={"question": "Help"}, headers=as_user(requester)).status_code == 403
    private = Queue(name="Restricted", restricted=True)
    db.add(private); db.flush(); ticket.queue = private; db.commit()
    assert client.get(url + "/context", headers=as_user(agent)).status_code == 404
    assert client.post(url, json={"question": "Help"}, headers=as_user(agent)).status_code == 404
    monkeypatch.setattr(settings, "copilot_enabled", False)
    assert not provider[1]


def test_whitespace_question_and_invalid_context_options_do_not_dispatch(client, agent, ticket, provider):
    for body in ({"question": "  "}, {"question": "Help", "include_ticket": False, "include_internal": True}):
        assert client.post(f"/api/tickets/{ticket.id}/copilot", headers=as_user(agent), json=body).status_code == 422
    assert not provider[1]


@pytest.mark.parametrize("payload", [[], {"messages": []}, {"messages": [None]}, {"messages": [{"text": ""}]}])
def test_unreadable_provider_answers_are_safe(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as http:
        with pytest.raises(copilot.CopilotError):
            copilot.ask("synthetic-token", "Help", [], "existing", http=http)


def test_conversation_metadata_migration_roundtrip(engine):
    cfg = alembic_config()
    assert inspect(engine).has_table("copilot_conversations")
    command.downgrade(cfg, "c3d58f1b7a20")
    try:
        assert not inspect(engine).has_table("copilot_conversations")
    finally:
        command.upgrade(cfg, "head")
    assert inspect(engine).has_table("copilot_conversations")
