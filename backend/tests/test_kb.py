"""Knowledge base: articles and who sees them, search and suggestions, and the ticket side."""

from app import kb
from app.models import Comment, KbArticle, Queue, Ticket, TicketEvent, TicketSource, TicketStatus, UserRole
from tests.conftest import as_user, make_user


def article(client, user, **over):
    body = {"title": "Outlook keeps asking for a password", "body": "## Fix\n\n1. Close Outlook\n2. Remove the saved **credentials**", "keywords": "mail prompt", "category": "Email", "audience": "everyone", "status": "published", **over}
    r = client.post("/api/kb/articles", json=body, headers=as_user(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_who_sees_what_and_search(client, db, agent, requester, admin):
    vpn = article(client, agent, title="Connect to the VPN from home", body="Open AnyConnect and sign in.", keywords="anyconnect remote access", category="Network")
    pw = article(client, agent)
    staff_only = article(client, agent, title="Reset the VPN appliance", body="Console cable, then...", audience="staff")
    draft = article(client, agent, title="VPN client upgrade", body="Push the new client.", keywords="", status="draft")
    assert pw["published_at"] and draft["published_at"] is None

    # Requesters: published articles for everyone, nothing else.
    seen = [a["title"] for a in client.get("/api/kb/articles", params={"q": "vpn"}, headers=as_user(requester)).json()]
    assert seen == ["Connect to the VPN from home"]
    assert client.get(f"/api/kb/articles/{staff_only['id']}", headers=as_user(requester)).status_code == 404
    assert client.get(f"/api/kb/articles/{draft['id']}", headers=as_user(requester)).status_code == 404
    mine = client.get(f"/api/kb/articles/{vpn['id']}", headers=as_user(requester)).json()
    assert mine["author"] is None and mine["keywords"] == "" and mine["views"] == 1 and mine["can_edit"] is False
    assert client.post("/api/kb/articles", json={"title": "x"}, headers=as_user(requester)).status_code == 403

    # Staff: drafts and staff-only too; title and keywords count most; prefixes match while typing.
    staff = [a["title"] for a in client.get("/api/kb/articles", params={"q": "vpn"}, headers=as_user(agent)).json()]
    assert set(staff) == {"Connect to the VPN from home", "Reset the VPN appliance", "VPN client upgrade"}
    assert [a["title"] for a in client.get("/api/kb/articles", params={"q": "anyconn"}, headers=as_user(agent)).json()] == ["Connect to the VPN from home"]
    assert client.get("/api/kb/articles", params={"q": "outlook password", "status": "draft"}, headers=as_user(agent)).json() == []
    found = client.get("/api/kb/articles", params={"q": "Outlook asks me for my password again"}, headers=as_user(requester)).json()
    assert found[0]["title"] == "Outlook keeps asking for a password" and "Remove the saved credentials" in found[0]["excerpt"]

    # Archived ones drop out of searches; feedback is counted; admins delete.
    client.put(f"/api/kb/articles/{draft['id']}", json={"title": "VPN client upgrade", "status": "archived"}, headers=as_user(agent))
    assert "VPN client upgrade" not in [a["title"] for a in client.get("/api/kb/articles", params={"q": "vpn"}, headers=as_user(agent)).json()]
    assert client.post(f"/api/kb/articles/{pw['id']}/feedback", json={"helpful": True}, headers=as_user(requester)).json()["helpful_yes"] == 1
    assert client.delete(f"/api/kb/articles/{pw['id']}", headers=as_user(agent)).status_code == 403
    assert client.delete(f"/api/kb/articles/{pw['id']}", headers=as_user(admin)).status_code == 204


def test_query_words():
    assert kb.tsquery_text("Printer's jammed, again!") == "printer:* | jammed:* | again:*"
    assert kb.tsquery_text("  ") is None


def test_ticket_side_suggestions_similar_links_and_draft(client, db, agent, requester):
    q = Queue(name="Desk", is_default=True)
    restricted = Queue(name="HR", restricted=True)
    db.add_all([q, restricted])
    db.flush()

    def ticket(subject, description="", status=TicketStatus.open, queue=q, category=None):
        t = Ticket(subject=subject, description=description, source=TicketSource.portal, queue_id=queue.id, requester_id=requester.id, status=status, category=category)
        db.add(t)
        db.flush()
        return t

    now = ticket("Outlook keeps prompting for password", "Since this morning Outlook asks for my password every few minutes", category="Email")
    old = ticket("Outlook password prompt every 5 minutes", "keeps asking", TicketStatus.resolved)
    ticket("Outlook prompting for password (HR)", "", TicketStatus.closed, queue=restricted)
    ticket("Printer jammed", "paper stuck", TicketStatus.closed)
    ticket("Outlook password prompts", "still open", TicketStatus.open)
    db.commit()
    pw = article(client, agent)
    article(client, agent, title="Printer paper jams", body="Open tray 2", keywords="", category="Printing")

    out = client.get(f"/api/tickets/{now.id}/kb", headers=as_user(agent)).json()
    assert [a["title"] for a in out["articles"]] == ["Outlook keeps asking for a password"]
    # Solved ones only, in queues the agent can see.
    assert [s["id"] for s in out["similar"]] == [old.id] and out["similar"][0]["label"] == f"#{old.id}"

    out = client.post(f"/api/tickets/{now.id}/kb/links", json={"article_id": pw["id"]}, headers=as_user(agent)).json()
    assert [a["id"] for a in out["linked"]] == [pw["id"]] and out["articles"] == []
    ev = db.query(TicketEvent).filter_by(ticket_id=now.id, type="kb_linked").one()
    assert ev.data["title"] == "Outlook keeps asking for a password"
    out = client.delete(f"/api/tickets/{now.id}/kb/links/{pw['id']}", headers=as_user(agent)).json()
    assert out["linked"] == []
    # Requesters don't get the ticket side.
    assert client.get(f"/api/tickets/{now.id}/kb", headers=as_user(requester)).status_code == 403

    # A draft from a solved ticket: the problem, the last reply as the fix, internal notes to tidy.
    db.add_all([
        Comment(ticket_id=old.id, author_id=agent.id, body="Cleared the cached credentials in Credential Manager.", is_internal=False, source=TicketSource.agent),
        Comment(ticket_id=old.id, author_id=agent.id, body="Was the old token after the password change.", is_internal=True, source=TicketSource.agent),
        Comment(ticket_id=old.id, author_id=requester.id, body="Thanks, works now", is_internal=False, source=TicketSource.portal),
    ])
    db.commit()
    d = client.post(f"/api/tickets/{old.id}/kb/draft", headers=as_user(agent)).json()
    assert d["status"] == "draft" and d["audience"] == "staff" and d["source_ticket_id"] == old.id and d["title"] == old.subject
    assert "## Problem\n\nkeeps asking" in d["body"] and "## Fix\n\nCleared the cached credentials" in d["body"]
    assert "- Was the old token" in d["body"] and "Thanks, works now" not in d["body"]
    assert db.query(KbArticle).count() == 3
    assert db.query(TicketEvent).filter_by(ticket_id=old.id, type="kb_linked").one().data["drafted"] is True


def test_portal_only_staff_read_like_requesters(client, db, agent):
    other = make_user(db, "x@corp.local", UserRole.agent, "X")
    db.commit()
    a = article(client, agent, audience="staff")
    other.portal_only = True
    assert not kb.visible(db.get(KbArticle, a["id"]), other)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_images_in_articles(client, db, agent, requester, admin):
    from app import storage
    from app.models import KbFile

    staff_only = article(client, agent, audience="staff")
    pub = article(client, agent, title="Printer", keywords="")
    up = client.post(f"/api/kb/articles/{pub['id']}/files", files={"file": ("tray 2.png", PNG, "image/png")}, headers=as_user(agent))
    assert up.status_code == 201, up.text
    img = up.json()
    assert img["markdown"] == f"![tray 2]({img['url']})"
    # Readers of the article get it, with headers that keep it an image.
    r = client.get(img["url"], headers=as_user(requester))
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    hidden = client.post(f"/api/kb/articles/{staff_only['id']}/files", files={"file": ("x.png", PNG, "image/png")}, headers=as_user(agent)).json()
    assert client.get(hidden["url"], headers=as_user(requester)).status_code == 404
    # Only real images, whatever they're called; no SVG; staff only.
    assert client.post(f"/api/kb/articles/{pub['id']}/files", files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")}, headers=as_user(agent)).status_code == 422
    assert client.post(f"/api/kb/articles/{pub['id']}/files", files={"file": ("x.png", PNG, "image/png")}, headers=as_user(requester)).status_code == 403
    # Deleting the article takes its images with it.
    key = db.query(KbFile).filter_by(article_id=pub["id"]).one().storage_key
    assert client.delete(f"/api/kb/articles/{pub['id']}", headers=as_user(admin)).status_code == 204
    assert db.query(KbFile).filter_by(article_id=pub["id"]).count() == 0 and not storage.path(key).exists()


def test_article_versions(client, db, agent, admin):
    a = article(client, agent, title="VPN", body="Open AnyConnect.", keywords="", status="draft")
    url = f"/api/kb/articles/{a['id']}"
    body = {"title": "VPN", "body": "Open AnyConnect.", "keywords": "", "audience": "everyone", "status": "draft"}
    # Saving without changing the text keeps one version.
    client.put(url, json={**body, "category": "Network"}, headers=as_user(agent))
    client.put(url, json={**body, "body": "Open AnyConnect and pick vpn.corp.example."}, headers=as_user(admin))
    versions = client.get(f"{url}/versions", headers=as_user(agent)).json()
    assert [v["number"] for v in versions] == [2, 1] and versions[0]["is_current"] and versions[0]["author"]["id"] == admin.id
    d = client.get(f"{url}/versions/2", headers=as_user(agent)).json()
    assert "-Open AnyConnect." in d["diff"] and "+Open AnyConnect and pick vpn.corp.example." in d["diff"]
    assert "+# VPN" in client.get(f"{url}/versions/1", headers=as_user(agent)).json()["diff"]
    # Restoring makes a new version with the old text.
    r = client.post(f"{url}/versions/1/restore", headers=as_user(agent))
    assert r.status_code == 200 and r.json()["body"] == "Open AnyConnect."
    versions = client.get(f"{url}/versions", headers=as_user(agent)).json()
    assert [(v["number"], v["note"]) for v in versions][0] == (3, "Back to version 1")
    # Restoring what it already says changes nothing.
    client.post(f"{url}/versions/3/restore", headers=as_user(agent))
    assert len(client.get(f"{url}/versions", headers=as_user(agent)).json()) == 3
    assert client.get(f"{url}/versions/9", headers=as_user(agent)).status_code == 404
