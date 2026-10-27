# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings > Branding: name, texts, theme, background, logo and favicon images."""

import pytest

from app.config import settings
from app.mail import outbox
from app.models import OutboundEmail, Queue, Ticket, TicketSource
from tests.conftest import as_user

JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
GIF = b"GIF89a" + b"\x00" * 200
ICO = b"\x00\x00\x01\x00\x01\x00" + b"\x00" * 200
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'


def put(client, admin, name, data, filename="bg.jpg"):
    return client.put(f"/api/settings/branding/files/{name}", files={"file": (filename, data, "image/jpeg")}, headers=as_user(admin))


def test_public_branding_without_signing_in(client, db):
    r = client.get("/api/branding")
    assert r.status_code == 200
    assert r.json() == {
        "product_name": "Layer 8", "hide_product_name": False, "center_brand_logo": False, "sign_in_message": "", "background_in_console": False, "portal_welcome": "",
        "theme_preset": "custom", "console_theme": "tidepool", "theme_brand": "#0f6b5c", "theme_sidebar": "#14211e", "dark_mode": "off",
        "background_url": None, "logo_url": None, "favicon_url": None,
        "sign_in_heading": "", "sign_in_layout": "center", "sign_in_card_size": "standard",
        "sign_in_background_fit": "cover", "sign_in_background_position": "center",
        "sign_in_background_overlay": 0, "sign_in_backdrop": "plain",
        "windows_sign_in_glow": True,
    }


def test_upload_background_and_logo(client, db, admin):
    r = put(client, admin, "background", GIF, "office.gif")
    assert r.status_code == 200, r.text
    url = r.json()["background_url"]
    assert url == "/api/branding/files/background?v=1"
    img = client.get(url)  # no session needed
    assert img.status_code == 200 and img.headers["content-type"] == "image/gif"
    assert img.headers["x-content-type-options"] == "nosniff" and "immutable" in img.headers["cache-control"]
    # A new upload gets a new URL, so cached copies don't linger.
    assert put(client, admin, "background", JPG).json()["background_url"] == "/api/branding/files/background?v=2"
    assert client.get("/api/branding/files/background").headers["content-type"] == "image/jpeg"
    assert put(client, admin, "logo", JPG).json()["logo_url"].startswith("/api/branding/files/logo")
    r = client.delete("/api/settings/branding/files/background", headers=as_user(admin))
    assert r.json()["background_url"] is None
    assert client.get("/api/branding/files/background").status_code == 404


def test_only_real_jpg_gif_png(client, admin, agent):
    for data, why in [(SVG, "JPG, GIF or PNG"), (b"MZ\x90\x00 an exe renamed .jpg", "JPG, GIF or PNG"), (b"\xff\xd8\xff" + bytes(9 * 1024 * 1024), "limited to 8 MB")]:
        r = put(client, admin, "background", data)
        assert r.status_code == 422 and why in r.json()["detail"]
    assert put(client, admin, "unknown", JPG).status_code == 422
    # ICO only for the favicon.
    assert put(client, admin, "logo", ICO).status_code == 422
    r = put(client, admin, "favicon", ICO, "favicon.ico")
    assert r.status_code == 200 and r.json()["favicon_url"] == "/api/branding/files/favicon?v=1"
    assert client.get(r.json()["favicon_url"]).headers["content-type"] == "image/x-icon"
    assert put(client, agent, "background", JPG).status_code == 403


def test_name_and_message(client, db, admin):
    r = client.put("/api/settings/branding", json={"values": {"product_name": "Example Help Desk", "sign_in_message": "Locked out? Call ext. 4400", "background_in_console": True}}, headers=as_user(admin))
    assert r.status_code == 200, r.text
    assert client.get("/api/branding").json()["product_name"] == "Example Help Desk"
    assert client.put("/api/settings/branding", json={"values": {"product_name": ""}}, headers=as_user(admin)).status_code == 422


def test_brand_visibility_and_alignment_are_optional_admin_settings(client, admin, agent):
    values = {"hide_product_name": True, "center_brand_logo": True}
    route = "/api/settings/branding"
    assert client.put(route, json={"values": values}, headers=as_user(agent)).status_code == 403
    assert client.put(route, json={"values": values}, headers=as_user(admin)).status_code == 200
    public = client.get("/api/branding").json()
    assert all(public[key] is True for key in values)
    assert public["product_name"] == "Layer 8"
    values = {key: False for key in values}
    assert client.put(route, json={"values": values}, headers=as_user(admin)).status_code == 200
    public = client.get("/api/branding").json()
    assert all(public[key] is False for key in values)


def test_signin_appearance_is_admin_only_validated_and_importable(client, db, admin, agent):
    from app import cli
    values = {"windows_sign_in_glow":False, "sign_in_layout":"right", "sign_in_card_size":"wide", "sign_in_heading":"Welcome to Example IT",
              "sign_in_background_fit":"contain", "sign_in_background_position":"left",
              "sign_in_background_overlay":45, "sign_in_backdrop":"ocean"}
    route="/api/settings/signin_appearance"
    assert client.put(route,json={"values":values},headers=as_user(agent)).status_code==403
    result=client.put(route,json={"values":values},headers=as_user(admin))
    assert result.status_code==200,result.text
    public=client.get("/api/branding").json()
    assert {key:public[key] for key in values}==values
    assert public["portal_welcome"]=="" and public["background_in_console"] is False
    for invalid in ({"sign_in_background_overlay":86},{"sign_in_background_overlay":-1},{"sign_in_layout":"url(evil)"},{"sign_in_heading":"x"*101},{"sign_in_background_fit":"stretch"}):
        assert client.put(route,json={"values":invalid},headers=as_user(admin)).status_code==422
    cli.import_settings(db,{"signin_appearance":{"sign_in_layout":"center","sign_in_backdrop":"plain"}})
    db.commit()
    assert client.get("/api/branding").json()["sign_in_layout"]=="center"


def test_theme_colours_need_contrast_with_white_text(client, db, admin):
    def save(**values):
        return client.put("/api/settings/theme", json={"values": values}, headers=as_user(admin))

    r = save(theme_brand="#1d4ed8", theme_sidebar="#111827", dark_mode="auto")
    assert r.status_code == 200, r.text
    assert {k: client.get("/api/branding").json()[k] for k in ("theme_brand", "theme_sidebar", "dark_mode")} == {"theme_brand": "#1d4ed8", "theme_sidebar": "#111827", "dark_mode": "auto"}
    r = save(theme_brand="#7dd3fc")
    assert r.status_code == 422 and "Too light" in r.json()["detail"] and "4.5:1" in r.json()["detail"]
    assert save(theme_sidebar="blue").status_code == 422
    assert save(dark_mode="sometimes").status_code == 422


def test_portal_welcome_and_email_footer(client, db, admin, requester, monkeypatch):
    r = client.put(
        "/api/settings/branding",
        json={"values": {"portal_welcome": "Open 8-5, Mon-Fri.", "email_footer": "IT Service Desk · ext. 4400"}},
        headers=as_user(admin),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/branding").json()["portal_welcome"] == "Open 8-5, Mon-Fri."
    monkeypatch.setattr(settings, "mailbox", "helpdesk@corp.local")
    queue = Queue(name="Desk", is_default=True)
    db.add(queue)
    db.flush()
    ticket = Ticket(subject="Printer", source=TicketSource.email, queue_id=queue.id, requester_id=requester.id)
    db.add(ticket)
    db.flush()
    assert outbox._queue(db, ticket, requester, "We're on it.")
    db.flush()
    assert db.query(OutboundEmail).one().body.endswith("\nIT Service Desk · ext. 4400")

def test_midnight_preset_is_optional_reversible_and_importable(client, db, admin, agent):
    from app import cli

    assert client.put("/api/settings/theme", headers=as_user(agent), json={"values": {"theme_preset": "midnight"}}).status_code == 403
    values = {"theme_brand": "#1d4ed8", "theme_sidebar": "#111827", "dark_mode": "auto"}
    assert client.put("/api/settings/theme", headers=as_user(admin), json={"values": values}).status_code == 200
    assert client.put("/api/settings/theme", headers=as_user(admin), json={"values": {"theme_preset": "midnight"}}).status_code == 200
    assert client.get("/api/branding").json()["theme_preset"] == "midnight"
    assert client.put("/api/settings/theme", headers=as_user(admin), json={"values": {"theme_preset": "unknown"}}).status_code == 422
    cli.import_settings(db, {"theme": {"theme_preset": "custom"}})
    db.commit()
    public = client.get("/api/branding").json()
    assert public["theme_preset"] == "custom"
    assert {k: public[k] for k in values} == values


@pytest.mark.parametrize("preset", ["ember", "ultraviolet"])
def test_ember_and_ultraviolet_are_choices_like_midnight(client, db, admin, preset):
    from app import cli

    assert client.put("/api/settings/theme", headers=as_user(admin), json={"values": {"theme_preset": preset}}).status_code == 200
    assert client.get("/api/branding").json()["theme_preset"] == preset
    cli.import_settings(db, {"theme": {"theme_preset": "custom"}})
    db.commit()
    assert client.get("/api/branding").json()["theme_preset"] == "custom"


def test_bad_environment_overrides_fall_back_instead_of_breaking_sign_in(client, monkeypatch):
    # HELPDESK_* values skip Settings' checks; the public branding call must still answer.
    for name, value in (("sign_in_layout", "url(evil)"), ("sign_in_backdrop", "neon"), ("theme_preset", "bogus"), ("dark_mode", "sometimes"), ("sign_in_background_overlay", 200)):
        monkeypatch.setattr(settings, name, value)
    public = client.get("/api/branding")
    assert public.status_code == 200
    body = public.json()
    assert (body["sign_in_layout"], body["sign_in_backdrop"], body["theme_preset"], body["dark_mode"], body["sign_in_background_overlay"]) == ("center", "plain", "custom", "off", 85)
