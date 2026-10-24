# SPDX-License-Identifier: AGPL-3.0-or-later
"""Knowledge base: articles (staff write them; requesters read published ones for everyone), and on a
ticket: matching articles, similar solved tickets, linking an article, drafting one from the ticket."""

import logging

import uuid

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select

from app import kb, numbering, permissions, scripts, services, sla, storage
from app.api.tickets import staff_ticket, visible_queue_ids
from app.auth import Agent, CurrentUser, SessionDep
from app.config import settings
from app.models import EventType, KbArticle, KbArticleTicket, KbArticleVersion, KbAudience, KbFile, KbStatus, User
from app.schemas import (
    KbArticleIn,
    KbArticleOut,
    KbArticleSummaryOut,
    KbFeedbackIn,
    KbFileOut,
    KbVersionDetailOut,
    KbVersionOut,
    KbLinkIn,
    SimilarTicketOut,
    TicketKbOut,
    UserRef,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge base"])
KbDeleter = permissions.require("kb_delete")


def _summary(a: KbArticle) -> KbArticleSummaryOut:
    out = KbArticleSummaryOut.model_validate(a)
    out.excerpt = kb.excerpt(a.body)
    return out


def _out(a: KbArticle, user: User) -> KbArticleOut:
    out = KbArticleOut.model_validate(a)
    out.excerpt = kb.excerpt(a.body)
    out.can_edit = kb.is_staff(user)
    if not kb.is_staff(user):
        # Who wrote it and from which ticket is the desk's business.
        out.source_ticket_id, out.author, out.updated_by, out.keywords = None, None, None, ""
    return out


def _article(session, article_id: int, user: User) -> KbArticle:
    a = session.get(KbArticle, article_id)
    if a is None or not kb.visible(a, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return a


def _staff(user: User) -> None:
    if not kb.is_staff(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only technicians and admins write articles")


@router.get("/api/kb/articles", response_model=list[KbArticleSummaryOut])
def list_articles(
    user: CurrentUser,
    session: SessionDep,
    q: str = Query("", max_length=500),
    category: str | None = Query(None, max_length=100),
    status_: KbStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=200),
):
    """Search (best match first) or browse (newest first). Requesters: published articles for everyone."""
    return [_summary(a) for a in kb.articles(session, user, q, category=category, status=status_, limit=limit)]


@router.get("/api/kb/articles/{article_id}", response_model=KbArticleOut)
def get_article(article_id: int, user: CurrentUser, session: SessionDep):
    a = _article(session, article_id, user)
    if a.status == KbStatus.published:
        a.views += 1
        session.commit()
    return _out(a, user)


def _apply(session, a: KbArticle, body: KbArticleIn, user: User) -> None:
    if KbStatus.published in (a.status, KbStatus(body.status)):
        # Publishing, or changing what's published, is a permission; drafts are anyone's.
        reason = permissions.not_allowed(session, user, "kb_publish")
        if reason:
            raise HTTPException(status.HTTP_403_FORBIDDEN, reason)
    changed = (a.title, a.body, a.keywords) != (body.title.strip(), body.body, body.keywords.strip())
    a.title, a.body, a.keywords = body.title.strip(), body.body, body.keywords.strip()
    a.category = (body.category or "").strip() or None
    a.audience, a.status = KbAudience(body.audience), KbStatus(body.status)
    if a.status == KbStatus.published and a.published_at is None:
        a.published_at = sla.utcnow()
    a.updated_by_id = user.id
    if changed:
        _new_version(session, a, user)


def _new_version(session, a: KbArticle, user: User, note: str = "") -> None:
    """Keep the text as it now is (a new article gets its id here, for its first version)."""
    session.add(a)
    session.flush()
    last = session.scalar(select(func.max(KbArticleVersion.number)).where(KbArticleVersion.article_id == a.id)) or 0
    session.add(KbArticleVersion(article_id=a.id, number=last + 1, title=a.title, body=a.body, keywords=a.keywords, note=note, author_id=user.id))


@router.post("/api/kb/articles", response_model=KbArticleOut, status_code=status.HTTP_201_CREATED)
def create_article(body: KbArticleIn, user: Agent, session: SessionDep):
    _staff(user)
    a = KbArticle(author_id=user.id)
    # Added once filled in (the permission check's query would otherwise flush it empty).
    _apply(session, a, body, user)
    session.add(a)
    session.commit()
    log.info("%s wrote article %r (%s)", user.display_name, a.title, a.status.value, extra={"source": "kb"})
    return _out(a, user)


@router.put("/api/kb/articles/{article_id}", response_model=KbArticleOut)
def update_article(article_id: int, body: KbArticleIn, user: Agent, session: SessionDep):
    _staff(user)
    a = _article(session, article_id, user)
    _apply(session, a, body, user)
    session.commit()
    return _out(a, user)


@router.delete("/api/kb/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_article(article_id: int, admin: KbDeleter, session: SessionDep):
    """Admins only; archiving keeps it (and its links from tickets)."""
    a = _article(session, article_id, admin)
    keys = list(session.scalars(select(KbFile.storage_key).where(KbFile.article_id == a.id)))
    session.delete(a)
    session.commit()
    for key in keys:
        storage.delete(key)
    log.info("%s deleted article %r", admin.display_name, a.title, extra={"source": "kb"})


@router.get("/api/kb/articles/{article_id}/versions", response_model=list[KbVersionOut])
def list_versions(article_id: int, user: Agent, session: SessionDep):
    """Newest first."""
    _staff(user)
    a = _article(session, article_id, user)
    rows = list(session.scalars(select(KbArticleVersion).where(KbArticleVersion.article_id == a.id).order_by(KbArticleVersion.number.desc())))
    return [_version(v, i == 0) for i, v in enumerate(rows)]


def _version(v: KbArticleVersion, current: bool) -> KbVersionOut:
    return KbVersionOut(number=v.number, title=v.title, note=v.note, author=UserRef.model_validate(v.author) if v.author else None, created_at=v.created_at, is_current=current)


def _one_version(session, a: KbArticle, number: int) -> KbArticleVersion:
    v = session.scalar(select(KbArticleVersion).where(KbArticleVersion.article_id == a.id, KbArticleVersion.number == number))
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version")
    return v


@router.get("/api/kb/articles/{article_id}/versions/{number}", response_model=KbVersionDetailOut)
def get_version(article_id: int, number: int, user: Agent, session: SessionDep):
    _staff(user)
    a = _article(session, article_id, user)
    v = _one_version(session, a, number)
    before = session.scalar(
        select(KbArticleVersion).where(KbArticleVersion.article_id == a.id, KbArticleVersion.number < number).order_by(KbArticleVersion.number.desc()).limit(1)
    )
    text = lambda x: f"# {x.title}\n\n{x.body}\n" if x else ""  # noqa: E731
    diff = scripts.diff(text(before), text(v), f"version {before.number}" if before else "nothing", f"version {v.number}")
    latest = session.scalar(select(func.max(KbArticleVersion.number)).where(KbArticleVersion.article_id == a.id))
    return KbVersionDetailOut(**_version(v, v.number == latest).model_dump(), body=v.body, keywords=v.keywords, diff=diff)


@router.post("/api/kb/articles/{article_id}/versions/{number}/restore", response_model=KbArticleOut)
def restore_version(article_id: int, number: int, user: Agent, session: SessionDep):
    """Bring an old version's text back, as a new version (history is never rewritten)."""
    _staff(user)
    a = _article(session, article_id, user)
    v = _one_version(session, a, number)
    newest = lambda: session.scalar(select(KbArticleVersion).where(KbArticleVersion.article_id == a.id).order_by(KbArticleVersion.number.desc()).limit(1))  # noqa: E731
    before = newest().number
    _apply(session, a, KbArticleIn(title=v.title, body=v.body, keywords=v.keywords, category=a.category, audience=a.audience.value, status=a.status.value), user)
    session.flush()
    if newest().number > before:
        newest().note = f"Back to version {number}"
    session.commit()
    return _out(a, user)


# Images: recognised by their first bytes, never by the name or the browser's word (and no SVG: it can hold script).
IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def _image_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((t for magic, t in IMAGE_TYPES if data.startswith(magic)), None)


@router.post("/api/kb/articles/{article_id}/files", response_model=KbFileOut, status_code=status.HTTP_201_CREATED)
def upload_image(article_id: int, user: Agent, session: SessionDep, file: UploadFile = File(...)):
    """An image for the article (PNG, JPEG, GIF or WebP). Returns the Markdown that shows it."""
    _staff(user)
    a = _article(session, article_id, user)
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Files are limited to {settings.max_upload_mb} MB")
    kind = _image_type(data)
    if kind is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only PNG, JPEG, GIF and WebP images can go in an article")
    name = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1][:255]
    f = KbFile(article_id=a.id, filename=name, content_type=kind, size=len(data), storage_key=storage.save(data).key, uploaded_by_id=user.id)
    session.add(f)
    session.commit()
    url = f"/api/kb/files/{f.id}"
    alt = name.rsplit(".", 1)[0].replace("]", "")
    return KbFileOut(id=str(f.id), filename=name, url=url, markdown=f"![{alt}]({url})")


@router.get("/api/kb/files/{file_id}")
def get_image(file_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    """An article's image, for whoever may read the article."""
    f = session.get(KbFile, file_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    _article(session, f.article_id, user)
    path = storage.path(f.storage_key)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Image is missing from storage")
    return Response(
        content=path.read_bytes(), media_type=f.content_type,
        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline", "Cache-Control": "private, max-age=3600", "Content-Security-Policy": "default-src 'none'"},
    )


@router.post("/api/kb/articles/{article_id}/feedback", response_model=KbArticleSummaryOut)
def article_feedback(article_id: int, body: KbFeedbackIn, user: CurrentUser, session: SessionDep):
    """Was this helpful? Counted, not tied to the person."""
    a = _article(session, article_id, user)
    if body.helpful:
        a.helpful_yes += 1
    else:
        a.helpful_no += 1
    session.commit()
    return _summary(a)


# On a ticket


@router.get("/api/tickets/{ticket_id}/kb", response_model=TicketKbOut)
def ticket_kb(ticket_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    linked = list(session.scalars(select(KbArticle).join(KbArticleTicket).where(KbArticleTicket.ticket_id == ticket.id).order_by(KbArticleTicket.linked_at)))
    text = f"{ticket.subject} {' '.join(ticket.description.split()[:40])}"
    found = [a for a in kb.articles(session, user, text, category=ticket.category, status=KbStatus.published, limit=8) if a not in linked][:5]
    similar = [
        SimilarTicketOut(id=t.id, label=numbering.label(t.id, t.created_at), subject=t.subject, status=t.status.value, category=t.category, resolved_at=t.resolved_at, score=round(r, 4))
        for t, r in kb.similar_tickets(session, ticket, visible_queue_ids(user))
    ]
    return TicketKbOut(articles=[_summary(a) for a in found], similar=similar, linked=[_summary(a) for a in linked])


@router.post("/api/tickets/{ticket_id}/kb/links", response_model=TicketKbOut)
def link_article(ticket_id: int, body: KbLinkIn, user: Agent, session: SessionDep):
    """Record that an article was used on the ticket (in its history)."""
    ticket = staff_ticket(session, ticket_id, user)
    a = _article(session, body.article_id, user)
    if session.get(KbArticleTicket, (a.id, ticket.id)) is None:
        session.add(KbArticleTicket(article_id=a.id, ticket_id=ticket.id, linked_by_id=user.id))
        services._event(session, ticket, EventType.kb_linked, user, article_id=a.id, title=a.title)
        session.commit()
    return ticket_kb(ticket_id, user, session)


@router.delete("/api/tickets/{ticket_id}/kb/links/{article_id}", response_model=TicketKbOut)
def unlink_article(ticket_id: int, article_id: int, user: Agent, session: SessionDep):
    ticket = staff_ticket(session, ticket_id, user)
    link = session.get(KbArticleTicket, (article_id, ticket.id))
    if link is not None:
        session.delete(link)
        session.commit()
    return ticket_kb(ticket_id, user, session)


@router.post("/api/tickets/{ticket_id}/kb/draft", response_model=KbArticleOut, status_code=status.HTTP_201_CREATED)
def draft_from_ticket(ticket_id: int, user: Agent, session: SessionDep):
    """A draft article (staff only, unpublished) from the ticket: its problem, the last reply as the fix, and notes."""
    _staff(user)
    ticket = staff_ticket(session, ticket_id, user)
    a = kb.draft_from_ticket(session, ticket, user)
    _new_version(session, a, user, f"Drafted from ticket {numbering.label(ticket.id, ticket.created_at)}")
    session.add(KbArticleTicket(article_id=a.id, ticket_id=ticket.id, linked_by_id=user.id))
    services._event(session, ticket, EventType.kb_linked, user, article_id=a.id, title=a.title, drafted=True)
    session.commit()
    log.info("%s drafted article %r from ticket %s", user.display_name, a.title, ticket.id, extra={"source": "kb"})
    return _out(a, user)
