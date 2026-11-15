# SPDX-License-Identifier: AGPL-3.0-or-later
"""The routes anyone can reach without signing in, pinned.

A new route without a user dependency fails this test until it is added to PUBLIC below, which
makes every addition to the unauthenticated surface a reviewed change (docs/security-review.md).
Each pinned route is then called with no credentials and with made-up tokens, as an outsider would.
"""

import uuid

import pytest

from app.auth import current_user, require_admin, require_agent
from app.config import settings
from app.main import app

USER_DEPENDENCIES = {current_user, require_agent, require_admin}

# Method + path -> what lets it through without a user session.
PUBLIC = {
    ("GET", "/api/health"): "liveness check",
    ("GET", "/api/about"): "version and licence (AGPL section 13)",
    ("GET", "/api/source"): "source download (AGPL section 13)",
    ("GET", "/{path:path}"): "the built web UI (static files)",
    ("POST", "/api/auth/login"): "sign-in (per-address and per-account limits)",
    ("POST", "/api/auth/logout"): "ends the caller's own session",
    ("GET", "/api/auth/options"): "which sign-in buttons to show",
    ("POST", "/api/auth/sso"): "Kerberos ticket, only when Windows sign-in is set up",
    ("GET", "/api/auth/entra/start"): "Entra sign-in: state + browser binding",
    ("GET", "/api/auth/entra/callback"): "Entra sign-in: state checked against the browser",
    ("GET", "/api/branding"): "sign-in page appearance",
    ("GET", "/api/branding/files/{name}"): "sign-in page images (named slots only)",
    ("GET", "/api/live"): "checks the session itself (staff only)",
    ("GET", "/api/integrations/tickets"): "API key or staff session",
    ("GET", "/api/integrations/tickets/{ref}"): "API key or staff session",
    ("POST", "/api/integrations/tickets/{ref}/notes"): "API key or staff session",
    ("GET", "/api/meraki/oauth/callback"): "OAuth state, hashed, single use",
    ("POST", "/api/meraki/webhooks"): "shared secret (32+ chars), off unless configured",
    ("POST", "/api/public/receiving/lookup"): "PO number + receiving code, rate-limited",
    ("POST", "/api/public/receiving/photo"): "PO number + receiving code, rate-limited",
    ("POST", "/api/public/receiving/receive"): "PO number + receiving code, rate-limited",
    ("GET", "/api/public/wallboard"): "wallboard bearer token (hashed)",
    ("POST", "/api/remote-sessions/{session_id}/end"): "one-time end token (hashed)",
    ("POST", "/api/runner/check-in"): "relay bearer token (hashed)",
    ("POST", "/api/runner/claim"): "relay bearer token (hashed)",
    ("POST", "/api/runner/runs/{run_id}/output"): "relay bearer token, own runs only",
    ("POST", "/api/runner/runs/{run_id}/finish"): "relay bearer token, own runs only",
    ("GET", "/api/surveys/answer/{token}"): "survey link token",
    ("POST", "/api/surveys/answer/{token}"): "survey link token",
    ("GET", "/api/update-progress/{request_id}.json"): "unguessable progress ID",
}


def _dependencies(dependant, found: set) -> set:
    for sub in dependant.dependencies:
        found.add(sub.call)
        _dependencies(sub, found)
    return found


def _routes(routes, prefix: str = ""):
    for route in routes:
        inner = None
        if type(route).__name__ == "_IncludedRouter":  # FastAPI keeps included routers nested
            inner = getattr(route, "router", None) or getattr(route, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes, prefix + (getattr(route, "prefix", "") or ""))
        elif hasattr(route, "dependant"):
            yield prefix, route


def public_routes() -> set[tuple[str, str]]:
    found = set()
    for prefix, route in _routes(app.routes):
        if not _dependencies(route.dependant, set()) & USER_DEPENDENCIES:
            found |= {(method, prefix + route.path) for method in route.methods if method != "HEAD"}
    return found


def test_unauthenticated_routes_are_the_reviewed_ones():
    found = public_routes()
    assert len(found) > 20, "the route walk found too little; has FastAPI's router layout changed?"
    assert found - PUBLIC.keys() == set(), "new route(s) without sign-in: review them, then add to PUBLIC"
    assert PUBLIC.keys() - found == set(), "PUBLIC lists route(s) that no longer exist or now need sign-in"


def test_dev_header_is_ignored_in_production(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "dev_auth", False)
    r = client.get("/api/auth/me", headers={"X-Dev-User": admin.email})
    assert r.status_code == 401


BOGUS = "Bearer " + "x" * 43
RID = uuid.uuid4().hex


@pytest.mark.parametrize(("method", "path", "kwargs", "expected"), [
    ("GET", "/api/live", {}, {401}),
    ("GET", "/api/integrations/tickets", {"headers": {"Authorization": "Bearer l8k_" + "x" * 43}}, {401}),
    ("GET", "/api/integrations/tickets", {}, {401}),
    ("POST", "/api/integrations/tickets/1/notes", {"json": {"body": "x"}}, {401, 403}),
    ("GET", "/api/public/wallboard", {"headers": {"Authorization": "Bearer l8w_" + "x" * 43}}, {401, 404}),
    ("GET", "/api/public/wallboard", {}, {401, 404}),
    ("POST", "/api/runner/claim", {"headers": {"Authorization": BOGUS}, "json": {"host": "h", "version": "1"}}, {401}),
    ("POST", "/api/runner/check-in", {"json": {"host": "h", "version": "1"}}, {401}),
    ("POST", "/api/runner/runs/1/output", {"headers": {"Authorization": BOGUS}, "json": {"text": "x"}}, {401}),
    ("POST", "/api/runner/runs/1/finish", {"headers": {"Authorization": BOGUS}, "json": {"exit_code": 0}}, {401, 422}),
    ("POST", "/api/remote-sessions/1/end?token=" + "x" * 32, {}, {404}),
    ("GET", "/api/surveys/answer/" + "x" * 32, {}, {404}),
    ("POST", "/api/surveys/answer/" + "x" * 32, {"json": {"rating": 5}}, {404, 422}),
    ("POST", "/api/meraki/webhooks", {"json": {"sharedSecret": ""}}, {503}),
    ("GET", "/api/meraki/oauth/callback?state=x&code=y", {}, {302, 303, 307, 400, 404}),
    ("GET", "/api/auth/entra/callback?state=x&code=y", {}, {302, 303, 307, 400, 404}),
    ("POST", "/api/auth/sso", {"headers": {"Authorization": "Negotiate eA=="}}, {404}),
    ("POST", "/api/public/receiving/lookup", {"json": {"po": "1", "code": "AAAAAA"}}, {404}),
    ("GET", "/api/branding/files/..%2f..%2fetc%2fpasswd", {}, {404}),
    ("GET", "/api/tickets", {}, {401}),
    ("GET", "/api/runners", {}, {401}),
])
def test_outsider_is_refused(client, monkeypatch, method, path, kwargs, expected):
    monkeypatch.setattr(settings, "dev_auth", False)
    r = client.request(method, path, follow_redirects=False, **kwargs)
    assert r.status_code in expected, (method, path, r.status_code, r.text[:200])
