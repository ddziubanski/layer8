"""Synthetic webhook payloads only; no Dashboard calls or live credentials."""
import asyncio
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from starlette.requests import Request

from app import configstore, meraki_webhooks as hooks, sla
from app.config import settings
from app.models import Comment, EventType, MerakiWebhookIncident as Incident, MerakiWebhookReceipt as Receipt
from app.models import OutboundEmail, Queue, Ticket, TicketEvent, TicketStatus
from tests.conftest import as_user

SECRET = "synthetic-webhook-secret-for-tests-only"
PATH = "/api/meraki/webhooks"


@pytest.fixture
def configured(monkeypatch):
    for name, value in dict(meraki_webhook_enabled=True, meraki_webhook_secret=SECRET,
            meraki_webhook_organization_ids=["123"], meraki_network_ids=["N_123"], meraki_webhook_queue_id=0,
            portal_url="https://helpdesk.corp.example", meraki_enabled=False).items():
        monkeypatch.setattr(settings, name, value)


def payload(**over):
    now = sla.utcnow()
    return dict(version="0.1", sharedSecret=SECRET, organizationId="123", networkId="N_123",
        deviceSerial="QXXX-EXAM-PLE1", alertId="alert-1", alertTypeId="stopped_reporting",
        occurredAt=(now - timedelta(minutes=2)).isoformat(), sentAt=now.isoformat(), **over)


def altered(**over):
    data = payload(); data.update(over); return data


def raw(data):
    return json.dumps(data).encode()


def test_pure_parser_discards_secrets_names_urls_and_arbitrary_alert_data(configured):
    result = hooks.parse(raw(altered(alertData={"password": "never-store"}, deviceName="[link](https://example.com)",
        organizationUrl="http://127.0.0.1/private")), sla.utcnow())
    assert set(result) == {"organization_id", "network_id", "serial", "alert_id", "alert_type", "occurred_at"}
    assert SECRET not in str(result) and "never-store" not in str(result)


@pytest.mark.parametrize("change,code", [({"sharedSecret": "bad"}, 401), ({"sharedSecret": None}, 401),
    ({"sharedSecret": "\ud800"}, 401), ({"organizationId": "other"}, 403), ({"networkId": "other"}, 403),
    ({"alertTypeId": []}, 422), ({"deviceSerial": ""}, 422), ({"alertId": "x" * 101}, 422),
    ({"version": "1.0"}, 422), ({"occurredAt": "2026-01-01"}, 422),
    ({"occurredAt": (sla.utcnow() + timedelta(days=1)).isoformat()}, 422)])
def test_pure_rejects_invalid_inputs_without_echoing_secrets(configured, change, code):
    with pytest.raises(HTTPException) as exc:
        hooks.parse(raw(altered(**change)), sla.utcnow())
    assert exc.value.status_code == code and SECRET not in str(exc.value.detail)


@pytest.mark.parametrize("data", [b"{", b"[]", b'{"sharedSecret":"one","sharedSecret":"two"}', b"\xff"])
def test_pure_invalid_json_is_fixed_error(configured, data):
    with pytest.raises(HTTPException) as exc: hooks.parse(data, sla.utcnow())
    assert exc.value.status_code == 400


def test_pure_old_or_unsupported_alerts_are_ignored(configured):
    assert hooks.parse(raw(altered(occurredAt=(sla.utcnow() - timedelta(days=9)).isoformat())), sla.utcnow()) is None
    assert hooks.parse(raw(altered(alertTypeId="settings_changed")), sla.utcnow()) is None


def test_pure_disabled_and_missing_configuration_fail_closed(configured, monkeypatch):
    for name, value in [("meraki_webhook_enabled", False), ("meraki_webhook_secret", "short"),
                        ("meraki_network_ids", []), ("meraki_webhook_organization_ids", []), ("portal_url", "http://helpdesk.corp.example")]:
        with monkeypatch.context() as context:
            context.setattr(settings, name, value)
            with pytest.raises(HTTPException) as exc: hooks.parse(raw(payload()), sla.utcnow())
            assert exc.value.status_code == 503


def test_pure_stream_limit_does_not_trust_content_length():
    chunks = iter([b"x" * 40000, b"x" * 40000])
    async def receive(): return {"type": "http.request", "body": next(chunks), "more_body": True}
    request = Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)
    with pytest.raises(HTTPException) as exc: asyncio.run(hooks.read_body(request))
    assert exc.value.status_code == 413


def test_db_retry_is_atomic_and_uses_normal_ticket_audit_and_sla(client, db, service_desk, slas, configured):
    data = payload()
    assert client.post(PATH, json=data).status_code == 204
    assert client.post(PATH, json={**data, "sentAt": sla.utcnow().isoformat()}).status_code == 204
    ticket = db.scalar(select(Ticket))
    assert ticket.queue_id == service_desk.id and ticket.resolution_due_at is not None
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert db.scalar(select(func.count()).select_from(OutboundEmail)) == 0
    assert db.scalar(select(TicketEvent).where(TicketEvent.type == EventType.created)) is not None
    receipt = db.scalar(select(Receipt)); assert receipt.outcome == "created" and receipt.ticket_id == ticket.id
    assert SECRET not in ticket.description


def test_db_recovery_is_internal_deduplicated_and_does_not_close(client, db, service_desk, configured):
    down = payload(); client.post(PATH, json=down)
    recovery = altered(alertId="recovery-1", alertTypeId="started_reporting", occurredAt=(sla.utcnow() - timedelta(minutes=1)).isoformat())
    assert client.post(PATH, json=recovery).status_code == 204
    assert client.post(PATH, json=recovery).status_code == 204
    ticket = db.scalar(select(Ticket)); assert ticket.status == TicketStatus.new
    notes = list(db.scalars(select(Comment))); assert len(notes) == 1 and notes[0].is_internal
    assert "recovery" in notes[0].body
    assert db.scalar(select(func.count()).select_from(OutboundEmail)) == 0


def test_db_out_of_order_and_equal_time_alerts_cannot_reopen_recovered_device(client, db, service_desk, configured):
    when = sla.utcnow() - timedelta(minutes=1)
    client.post(PATH, json=altered(alertId="up", alertTypeId="started_reporting", occurredAt=when.isoformat()))
    client.post(PATH, json=altered(alertId="old", occurredAt=(when - timedelta(seconds=1)).isoformat()))
    client.post(PATH, json=altered(alertId="tie", occurredAt=when.isoformat()))
    assert db.scalar(select(func.count()).select_from(Ticket)) == 0
    assert [r.outcome for r in db.scalars(select(Receipt).order_by(Receipt.received_at))] == ["unmatched_recovery", "stale", "stale"]


def test_db_new_outages_reuse_open_ticket_but_create_after_manual_close(client, db, service_desk, configured):
    data = payload(); client.post(PATH, json=data)
    first = db.scalar(select(Ticket))
    client.post(PATH, json=altered(alertId="second", occurredAt=(sla.utcnow() - timedelta(minutes=1)).isoformat()))
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    first.status = TicketStatus.closed; db.commit()
    client.post(PATH, json=altered(alertId="third", occurredAt=sla.utcnow().isoformat()))
    assert db.scalar(select(func.count()).select_from(Ticket)) == 2


def test_db_conflicting_replay_and_invalid_scope_make_no_changes(client, db, service_desk, configured):
    data = payload(); client.post(PATH, json=data)
    assert client.post(PATH, json={**data, "alertTypeId": "started_reporting"}).status_code == 409
    assert client.post(PATH, json=altered(networkId="N_other")).status_code == 403
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert db.scalar(select(func.count()).select_from(Comment)) == 0


def test_db_invalid_queue_rolls_back_receipt_and_ticket(client, db, service_desk, configured, monkeypatch):
    monkeypatch.setattr(settings, "meraki_webhook_queue_id", 99999)
    assert client.post(PATH, json=payload()).status_code == 503
    assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    assert db.scalar(select(func.count()).select_from(Incident)) == 0
    assert db.scalar(select(func.count()).select_from(Ticket)) == 0


def test_db_fallback_queue_and_delivery_history_permissions(client, db, service_desk, configured, admin, agent, requester, monkeypatch):
    queue = Queue(name="Network operations"); db.add(queue); db.commit()
    monkeypatch.setattr(settings, "meraki_webhook_queue_id", queue.id)
    assert client.post(PATH, json=payload()).status_code == 204
    assert db.scalar(select(Ticket)).queue_id == queue.id
    status_path = PATH + "/status"
    assert client.get(status_path).status_code == 401
    for user in (agent, requester): assert client.get(status_path, headers=as_user(user)).status_code == 403
    response = client.get(status_path, headers=as_user(admin))
    assert response.status_code == 200 and len(response.json()["deliveries"]) == 1
    assert SECRET not in response.text and "fingerprint" not in response.text


def test_db_limits_and_busy_response_create_no_side_effects(client, db, service_desk, configured, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_PER_MINUTE", 1)
    data = payload(); client.post(PATH, json=data)
    assert client.post(PATH, json=data).status_code == 204  # Replay succeeds even at the rate limit.
    response = client.post(PATH, json=altered(alertId="second"))
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": hooks.LOCK})
    try: assert client.post(PATH, json=altered(alertId="third")).status_code == 503
    finally: db.rollback()


def test_db_settings_api_and_cli_import_use_write_only_secret_file(client, db, admin, agent):
    from app.cli import import_settings
    response = client.put("/api/settings/meraki", headers=as_user(admin), json={"values": {
        "meraki_webhook_enabled": True, "meraki_webhook_secret": SECRET,
        "meraki_webhook_organization_ids": ["123"], "meraki_webhook_queue_id": 0}})
    assert response.status_code == 200 and SECRET not in response.text
    assert configstore._secret_path("meraki_webhook_secret").read_text() == SECRET
    assert client.put("/api/settings/meraki", headers=as_user(agent), json={"values": {"meraki_webhook_enabled": False}}).status_code == 403
    assert client.put("/api/settings/meraki", headers=as_user(admin), json={"values": {"meraki_webhook_secret": "short"}}).status_code == 422
    import_settings(db, {"meraki": {"meraki_webhook_enabled": False, "meraki_webhook_organization_ids": ["456"]}})
    assert not settings.meraki_webhook_enabled and settings.meraki_webhook_organization_ids == ["456"]


def test_db_routing_rule_overrides_fallback_queue(client, db, service_desk, configured, monkeypatch):
    from app.models import RoutingRule, TicketSource
    fallback, routed = Queue(name="Fallback"), Queue(name="Routed network team")
    db.add_all([fallback, routed]); db.flush()
    db.add(RoutingRule(name="Meraki outages", match_keywords=["Meraki device"], match_source=TicketSource.api, set_queue_id=routed.id))
    db.commit(); monkeypatch.setattr(settings, "meraki_webhook_queue_id", fallback.id)
    assert client.post(PATH, json=payload()).status_code == 204
    assert db.scalar(select(Ticket)).queue_id == routed.id


def test_db_concurrent_retry_cannot_create_a_second_ticket(db, session_factory, service_desk, configured, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    original = hooks.services.create_ticket
    def create(*args, **kwargs):
        entered.set(); assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(hooks.services, "create_ticket", create)
    data = raw(payload())
    def deliver():
        with session_factory() as session: hooks.receive(session, data)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(deliver)
        try:
            assert entered.wait(10)
            with pytest.raises(HTTPException) as exc: deliver()
            assert exc.value.status_code == 503
        finally: release.set()
        first.result(timeout=10)
    deliver()
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_db_capacity_and_retention_are_bounded(client, db, service_desk, configured, monkeypatch):
    data = payload(); client.post(PATH, json=data)
    row = db.scalar(select(Receipt)); row.received_at = sla.utcnow() - timedelta(days=9); db.commit()
    monkeypatch.setattr(hooks, "MAX_RECEIPTS", 1)
    assert client.post(PATH, json=altered(alertId="replacement")).status_code == 204
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    assert client.post(PATH, json=altered(alertId="over-capacity")).status_code == 503
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1


@pytest.mark.parametrize("headers,body,code", [({"Content-Type": "text/plain"}, b"{}", 415),
    ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, b"{}", 415),
    ({"Content-Type": "application/json"}, b"x" * (hooks.MAX_BODY + 1), 413)])
def test_db_http_body_rejections(client, configured, headers, body, code):
    assert client.post(PATH, content=body, headers=headers).status_code == code


def test_db_migration_roundtrip_matches_metadata(engine):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models import Base
    from tests.conftest import alembic_config
    command.downgrade(alembic_config(), "b97e31a50d82")
    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
