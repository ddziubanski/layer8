# SPDX-License-Identifier: AGPL-3.0-or-later
from app.config import settings
from tests.conftest import ANSWERS, as_user, make_user


def new_ticket(client, user):
    return client.post("/api/portal/tickets", json={"subject": "Printer jam", **ANSWERS}, headers=as_user(user)).json()["id"]


def upload(client, user, ticket_id, name="log.txt", data=b"timeout", comment_id=None, content_type="text/plain"):
    form = {"comment_id": str(comment_id)} if comment_id is not None else {}
    return client.post(
        f"/api/tickets/{ticket_id}/attachments",
        files={"file": (name, data, content_type)},
        data=form,
        headers=as_user(user),
    )


def test_upload_list_download_and_count(client, agent, requester, service_desk):
    tid = new_ticket(client, requester)
    r = upload(client, requester, tid, name="naïve report.html", data=b"<script>x</script>", content_type="text/html")
    assert r.status_code == 201, r.text
    att = r.json()
    assert (att["filename"], att["size_bytes"], att["uploaded_by_id"]) == ("naïve report.html", 18, requester.id)

    listed = client.get(f"/api/tickets/{tid}/attachments", headers=as_user(agent)).json()
    assert [a["id"] for a in listed] == [att["id"]]

    d = client.get(f"/api/attachments/{att['id']}/download", headers=as_user(requester))
    assert d.status_code == 200
    assert d.content == b"<script>x</script>"
    # Never rendered in the browser.
    assert d.headers["content-disposition"] == "attachment; filename*=UTF-8''na%C3%AFve%20report.html"
    assert d.headers["x-content-type-options"] == "nosniff"
    assert d.headers["content-security-policy"] == "sandbox"

    ticket = client.get(f"/api/tickets/{tid}", headers=as_user(agent)).json()
    assert ticket["attachment_count"] == 1
    events = client.get(f"/api/tickets/{tid}/events", headers=as_user(agent)).json()
    assert events[-1]["type"] == "attachment_added"
    assert events[-1]["data"]["filename"] == "naïve report.html"


def test_internal_note_files_are_hidden_from_requester(client, agent, requester, service_desk):
    tid = new_ticket(client, requester)
    note = client.post(f"/api/tickets/{tid}/comments", json={"body": "switch config", "is_internal": True}, headers=as_user(agent)).json()
    secret = upload(client, agent, tid, name="running-config.txt", comment_id=note["id"]).json()
    public = upload(client, agent, tid, name="steps.pdf").json()

    seen = client.get(f"/api/tickets/{tid}/attachments", headers=as_user(requester)).json()
    assert [a["filename"] for a in seen] == ["steps.pdf"]
    assert client.get(f"/api/attachments/{secret['id']}/download", headers=as_user(requester)).status_code == 404
    assert client.get(f"/api/attachments/{public['id']}/download", headers=as_user(requester)).status_code == 200
    assert len(client.get(f"/api/tickets/{tid}/attachments", headers=as_user(agent)).json()) == 2
    # The count only includes what the requester could see.
    assert client.get(f"/api/portal/tickets/{tid}", headers=as_user(requester)).json()["attachment_count"] == 1
    listed = client.get("/api/tickets", headers=as_user(agent)).json()["items"][0]
    assert listed["attachment_count"] == 1


def test_requesters_only_reach_their_own_tickets_files(client, db, requester, service_desk):
    other = make_user(db, "tnguyen@corp.local")
    tid = new_ticket(client, other)
    att = upload(client, other, tid).json()
    assert upload(client, requester, tid).status_code == 404
    assert client.get(f"/api/tickets/{tid}/attachments", headers=as_user(requester)).status_code == 404
    assert client.get(f"/api/attachments/{att['id']}/download", headers=as_user(requester)).status_code == 404


def test_comment_must_match_ticket_and_requesters_only_attach_to_their_own(client, agent, requester, service_desk):
    t1, t2 = new_ticket(client, requester), new_ticket(client, requester)
    agent_reply = client.post(f"/api/tickets/{t1}/comments", json={"body": "hi"}, headers=as_user(agent)).json()
    assert upload(client, agent, t2, comment_id=agent_reply["id"]).status_code == 409
    assert upload(client, requester, t1, comment_id=agent_reply["id"]).status_code == 409
    assert upload(client, requester, t1, comment_id=999999).status_code == 404


def test_size_limit(client, requester, service_desk, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    tid = new_ticket(client, requester)
    r = upload(client, requester, tid, data=b"x" * (1024 * 1024 + 1))
    assert r.status_code == 413
    assert upload(client, requester, tid, data=b"x" * (1024 * 1024)).status_code == 201


def test_missing_file_on_disk_is_410(client, db, requester, service_desk):
    from app import storage
    from app.models import Attachment

    tid = new_ticket(client, requester)
    att = upload(client, requester, tid).json()
    storage.delete(db.get(Attachment, att["id"]).storage_key)
    assert client.get(f"/api/attachments/{att['id']}/download", headers=as_user(requester)).status_code == 410


def test_view_previews_safe_types_only(client, agent, requester, service_desk):
    tid = new_ticket(client, requester)
    png = upload(client, requester, tid, name="shot.png", data=b"\x89PNG", content_type="image/png").json()
    log = upload(client, requester, tid, name="ping.log", data=b"timeout", content_type="application/octet-stream").json()
    pdf = upload(client, requester, tid, name="quote.pdf", data=b"%PDF-1.4", content_type="application/pdf").json()
    html = upload(client, requester, tid, name="page.html", data=b"<script>alert(1)</script>", content_type="text/html").json()
    svg = upload(client, requester, tid, name="logo.svg", data=b"<svg onload=alert(1)>", content_type="image/svg+xml").json()
    # Lies about its type: claims to be an image.
    sneaky = upload(client, requester, tid, name="x.png", data=b"<script>alert(1)</script>", content_type="image/png").json()

    assert [png["preview"], log["preview"], pdf["preview"], html["preview"], svg["preview"]] == ["image", "text", "pdf", None, None]
    assert png["uploaded_by"]["display_name"] == "R. Alvarez"

    view = lambda a: client.get(f"/api/attachments/{a['id']}/view", headers=as_user(agent))
    r = view(png)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["content-security-policy"].startswith("sandbox;")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert view(log).headers["content-type"] == "text/plain; charset=utf-8"
    p = view(pdf)
    assert p.headers["content-type"] == "application/pdf" and "default-src 'none'" in p.headers["content-security-policy"]
    assert view(html).status_code == 415 and view(svg).status_code == 415
    # nosniff + image type: the browser won't run it as HTML.
    s = view(sneaky)
    assert s.headers["content-type"] == "image/png" and s.headers["x-content-type-options"] == "nosniff"


def test_view_follows_visibility_rules(client, db, agent, requester, service_desk):
    tid = new_ticket(client, requester)
    note = client.post(f"/api/tickets/{tid}/comments", json={"body": "cfg", "is_internal": True}, headers=as_user(agent)).json()
    secret = upload(client, agent, tid, name="cfg.txt", comment_id=note["id"]).json()
    assert client.get(f"/api/attachments/{secret['id']}/view", headers=as_user(requester)).status_code == 404
    assert client.get(f"/api/attachments/{secret['id']}/view", headers=as_user(agent)).status_code == 200
