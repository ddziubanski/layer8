import json
from datetime import timedelta

import httpx
import pytest

from app import configstore, sla, teams
from app.config import settings
from app.models import Queue, Ticket, TicketSource, TicketStatus
from tests.conftest import as_user

URL = "https://demo.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/demo/triggers/manual/paths/invoke?sig=test-only-secret"


@pytest.mark.parametrize("url", [URL, "https://prod.logic.azure.com/workflows/demo/triggers/manual/paths/invoke?sig=sample"])
def test_workflow_hosts(url):
    teams.validate_url(url)


@pytest.mark.parametrize("url", ["http://prod.logic.azure.com/workflows/x", "https://localhost/workflows/x", "https://127.0.0.1/workflows/x", "https://prod.logic.azure.com.evil.example/workflows/x", "https://user:pass@prod.logic.azure.com/workflows/x", "https://prod.logic.azure.com:8443/workflows/x", "https://prod.logic.azure.com/wrong", "https://prod.logic.azure.com/workflows/x#secret", "https://evil.example\\@prod.logic.azure.com/workflows/x"])
def test_rejects_other_destinations(url):
    with pytest.raises(teams.TeamsError):
        teams.validate_url(url)


def test_delivery_payload_and_safe_http_errors(monkeypatch):
    monkeypatch.setattr(settings, "teams_webhook_url", URL)
    original = httpx.Client
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example"})

    def client(**kwargs):
        assert kwargs == {"timeout": 10, "follow_redirects": False, "trust_env": False}
        return original(transport=httpx.MockTransport(receive), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(teams.TeamsError, match="HTTP 302") as exc:
        teams.send(teams.card({"Open tickets": 3}))
    assert "sig=" not in str(exc.value) and len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["attachments"][0]["content"]["body"][1]["facts"] == [{"title": "Open tickets", "value": "3"}]


def test_network_errors_never_include_signed_url(monkeypatch):
    monkeypatch.setattr(settings, "teams_webhook_url", URL)
    def fail(*args, **kwargs):
        raise httpx.ConnectError(URL)
    monkeypatch.setattr(httpx.Client, "post", fail)
    with pytest.raises(teams.TeamsError) as exc:
        teams.send({})
    assert "test-only-secret" not in str(exc.value) and "sig=" not in str(exc.value)


def test_disabled_or_incomplete_never_sends(monkeypatch):
    monkeypatch.setattr(settings, "teams_enabled", False)
    monkeypatch.setattr(teams, "send", lambda _: pytest.fail("must not send"))
    assert teams.run(None)[1] is False
    monkeypatch.setattr(settings, "teams_enabled", True)
    monkeypatch.setattr(settings, "teams_webhook_url", "")
    assert teams.run(None)[1] is False


def test_summary_excludes_restricted_and_closed_tickets(db, requester, monkeypatch):
    public, private = Queue(name="Public"), Queue(name="Private", restricted=True)
    db.add_all([public, private]); db.flush()
    for queue, status in [(public, TicketStatus.open), (public, TicketStatus.closed), (private, TicketStatus.open)]:
        db.add(Ticket(subject="Never send this subject", requester_id=requester.id, queue_id=queue.id, source=TicketSource.portal, status=status, sla_at_risk_at=sla.utcnow() - timedelta(minutes=1)))
    db.commit()
    values = teams.counts(db)
    assert values == {"Open tickets": 1, "Unassigned": 1, "SLA at risk (including breached)": 1, "Waiting on user": 0}
    monkeypatch.setattr(settings, "teams_enabled", True)
    monkeypatch.setattr(settings, "teams_webhook_url", URL)
    sent = []
    monkeypatch.setattr(teams, "send", sent.append)
    assert teams.run(db)[1] is True
    assert "Never send" not in json.dumps(sent) and requester.email not in json.dumps(sent)


def test_settings_write_only_admin_and_importable(client, db, admin, agent):
    assert client.get("/api/settings/teams", headers=as_user(agent)).status_code == 403
    assert client.put("/api/settings/teams", json={"values": {"teams_enabled": True}}, headers=as_user(agent)).status_code == 403
    configstore.update(db, "teams", {"teams_webhook_url": URL, "teams_summary_minutes": 30}, admin)
    r = client.get("/api/settings/teams", headers=as_user(admin))
    assert r.status_code == 200 and "test-only-secret" not in r.text
    fields = {f["name"]: f for f in r.json()["fields"]}
    assert fields["teams_webhook_url"]["is_set"] is True
    assert fields["teams_summary_minutes"]["value"] == 30
    with pytest.raises(configstore.SettingsError):
        configstore.update(db, "teams", {"teams_webhook_url": "https://evil.example"}, admin)
    with pytest.raises(configstore.SettingsError):
        configstore.update(db, "teams", {"teams_summary_minutes": 1}, admin)
