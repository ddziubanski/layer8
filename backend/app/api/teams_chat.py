"""Technician-owned Microsoft connections and explicit ticket messages."""
import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import services, teams_chat
from app.config import settings
from app.api.tickets import staff_ticket
from app.auth import Agent, COOKIE, SessionDep
from app.models import EventType, TeamsDelivery, TicketSource, User

router = APIRouter(tags=["teams"])


class ConnectionOut(BaseModel):
    configured: bool
    connected: bool
    account: str | None = None
    # The connection includes the Copilot permissions (Settings > Teams > Copilot on tickets).
    copilot: bool = False


class StartOut(BaseModel):
    url: str


class CallbackIn(BaseModel):
    state: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=8192)
    error: str | None = Field(default=None, max_length=256)


class ChatOut(BaseModel):
    id: str
    recipient: str


class SendIn(BaseModel):
    delivery_id: UUID
    chat_id: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=8000)


class SentOut(BaseModel):
    comment_id: int


def lock(session, user):
    # Serialize token-cache rotation, disconnect and duplicate sends across processes.
    session.scalar(select(User).where(User.id == user.id).with_for_update())


def failure(exc):
    return HTTPException(409, str(exc))


@router.get("/api/teams/connection", response_model=ConnectionOut)
def connection(user: Agent):
    return teams_chat.status(user.id)


@router.post("/api/teams/connect", response_model=StartOut)
def connect(user: Agent, request: Request, session: SessionDep):
    lock(session, user)
    try:
        return {"url": teams_chat.begin(user, request.cookies.get(COOKIE))}
    except teams_chat.ConnectionError as exc:
        raise failure(exc) from None


@router.post("/api/teams/callback", response_model=ConnectionOut)
def callback(body: CallbackIn, user: Agent, request: Request, session: SessionDep):
    lock(session, user)
    try:
        teams_chat.finish(user, request.cookies.get(COOKIE), body.model_dump(exclude_none=True))
        return teams_chat.status(user.id)
    except teams_chat.ConnectionError as exc:
        raise failure(exc) from None


@router.delete("/api/teams/connection", status_code=204)
def disconnect(user: Agent, session: SessionDep):
    lock(session, user)
    teams_chat.disconnect(user.id)


@router.get("/api/tickets/{ticket_id}/teams/chats", response_model=list[ChatOut])
def chats(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    lock(session, user)
    try:
        token, record = teams_chat.access(user.id)
        return teams_chat.chats(token, ticket.requester, record["oid"])
    except teams_chat.ConnectionError as exc:
        raise failure(exc) from None


@router.post("/api/tickets/{ticket_id}/teams/messages", response_model=SentOut)
def send(ticket_id: int, body: SendIn, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    if not body.body.strip():
        raise HTTPException(422, "Write a Teams message first")
    lock(session, user)
    digest = hashlib.sha256(body.body.encode()).hexdigest()
    previous = session.get(TeamsDelivery, str(body.delivery_id))
    if previous:
        if previous.actor_id != user.id or previous.ticket_id != ticket.id or previous.data.get("digest") != digest or previous.data.get("chat_id") != body.chat_id:
            raise HTTPException(409, "This send identifier was already used for another message")
        if previous.data.get("teams_status") == "sent":
            return {"comment_id": previous.data["teams_comment_id"]}
        raise HTTPException(409, "This message may already have reached Teams. Check Teams before starting a new send")
    try:
        token, record = teams_chat.access(user.id)
        allowed = teams_chat.chats(token, ticket.requester, record["oid"])
        if not any(chat["id"] == body.chat_id for chat in allowed):
            raise HTTPException(409, "Choose an existing one-to-one chat with this ticket's requester")
    except teams_chat.ConnectionError as exc:
        raise failure(exc) from None
    event = TeamsDelivery(id=str(body.delivery_id), ticket_id=ticket.id, actor_id=user.id, data={
        "teams_delivery_id": str(body.delivery_id), "teams_status": "pending", "chat_id": body.chat_id,
        "digest": digest, "recipient": ticket.requester.upn or ticket.requester.email,
        "sender": record["account"],
    })
    session.add(event)
    services._event(session, ticket, EventType.updated, user, **{**event.data, "teams_status": "started"})
    session.commit()  # Durable intent before the external side effect: never silently resend.
    lock(session, user)
    try:
        result = teams_chat.send(token, body.chat_id, body.body)
        if not result.get("id"):
            raise teams_chat.ConnectionError("Teams did not confirm delivery; check Teams before sending again")
    except teams_chat.ConnectionError as exc:
        event.data = {**event.data, "teams_status": "unknown"}
        services._event(session, ticket, EventType.updated, user, **event.data)
        session.commit()
        raise failure(exc) from None
    comment = services.add_comment(session, ticket, user, body=body.body, is_internal=False,
                                   source=TicketSource.agent, as_staff=True, email_requester=False)
    event.data = {**event.data, "teams_status": "sent", "teams_message_id": result["id"], "teams_comment_id": comment.id}
    services._event(session, ticket, EventType.updated, user, **event.data)
    session.commit()
    return {"comment_id": comment.id}


# Microsoft 365 Copilot on a ticket (app.copilot)


class CopilotIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    include_ticket: bool = True
    # Internal notes require a separate, explicit choice.
    include_internal: bool = False
    # Existing API callers may omit the digest; the UI always supplies its reviewed preview.
    context_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    conversation_id: str | None = Field(default=None, min_length=1, max_length=200)


class CopilotContextOut(BaseModel):
    sections: list[str]
    characters: int
    limit: int
    digest: str


class CitationOut(BaseModel):
    title: str
    url: str


class CopilotOut(BaseModel):
    conversation_id: str
    answer: str
    citations: list[CitationOut]


def copilot_context(session, ticket, include_internal):
    from app import copilot
    from app.models import Comment
    query = select(Comment).where(Comment.ticket_id == ticket.id)
    if not include_internal:
        query = query.where(Comment.is_internal.is_(False))
    comments = list(session.scalars(query.order_by(Comment.created_at.desc(), Comment.id.desc()).limit(copilot.LAST_COMMENTS)))
    return copilot.ticket_context(ticket, list(reversed(comments)), include_internal=include_internal)


@router.get("/api/tickets/{ticket_id}/copilot/context", response_model=CopilotContextOut)
def preview_copilot_context(ticket_id: int, user: Agent, session: SessionDep, include_internal: bool = Query(False)):
    from app import copilot
    ticket = staff_ticket(session, ticket_id, user)
    if not settings.copilot_enabled:
        raise HTTPException(409, "Copilot on tickets is off (Settings > Teams)")
    context = copilot_context(session, ticket, include_internal)
    return {"sections": context, "characters": sum(map(len, context)), "limit": copilot.MAX_CONTEXT,
            "digest": copilot.context_digest(context)}


@router.post("/api/tickets/{ticket_id}/copilot", response_model=CopilotOut)
def ask_copilot(ticket_id: int, body: CopilotIn, user: Agent, session: SessionDep):
    """Ask as yourself; bind follow-ups and review the bounded context before sharing it."""
    from app import copilot
    from app.models import CopilotConversation, UserRole

    ticket = staff_ticket(session, ticket_id, user)
    if not settings.copilot_enabled:
        raise HTTPException(409, "Copilot on tickets is off (Settings > Teams)")
    if not body.question.strip():
        raise HTTPException(422, "Write a question for Copilot first")
    if body.include_internal and not body.include_ticket:
        raise HTTPException(422, "Include ticket text before choosing internal notes")
    context = copilot_context(session, ticket, body.include_internal) if body.include_ticket else []
    digest = copilot.context_digest(context)
    if body.include_ticket and body.context_digest is not None and body.context_digest != digest:
        raise HTTPException(409, "Ticket context changed. Review the updated preview before asking again")
    lock(session, user)
    try:
        token, record = teams_chat.access(user.id, teams_chat.COPILOT_SCOPES)
        identity = copilot.account_key(record)
    except (teams_chat.ConnectionError, copilot.CopilotError):
        raise HTTPException(409, "Reconnect your Microsoft account in My preferences to allow Copilot") from None
    conversation = None
    if body.conversation_id:
        conversation = session.scalar(select(CopilotConversation).where(
            CopilotConversation.conversation_id == body.conversation_id,
            CopilotConversation.ticket_id == ticket.id,
            CopilotConversation.user_id == user.id,
            CopilotConversation.account_key == identity,
        ))
        if conversation is None:
            raise HTTPException(409, "Start a new Copilot conversation for this ticket and Microsoft account")
        if (conversation.shared_ticket, conversation.shared_internal) != (body.include_ticket, body.include_internal):
            raise HTTPException(409, "Start a new conversation when changing what you share; previous context may remain in Microsoft 365")
    session.commit()  # Release token-cache locks before the slow provider call.
    try:
        result = copilot.ask(token, body.question.strip(), context, body.conversation_id)
    except copilot.CopilotError as exc:
        raise HTTPException(502, str(exc)) from None
    session.refresh(user)
    if not user.is_active or user.role not in (UserRole.agent, UserRole.admin):
        raise HTTPException(403, "You no longer have technician access")
    session.expire_all()
    ticket = staff_ticket(session, ticket_id, user)
    if conversation is None:
        session.add(CopilotConversation(conversation_id=result["conversation_id"], user_id=user.id,
            ticket_id=ticket.id, account_key=identity, shared_ticket=body.include_ticket,
            shared_internal=body.include_internal))
    services._event(session, ticket, EventType.updated, user, copilot="asked",
                    shared_ticket=body.include_ticket, shared_internal=body.include_internal,
                    context_digest=digest if body.include_ticket else None)
    session.commit()
    return result
