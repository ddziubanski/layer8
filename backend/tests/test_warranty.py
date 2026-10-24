# SPDX-License-Identifier: AGPL-3.0-or-later
"""Warranty lookups with Dell TechDirect and Lenovo's warranty API (mocked HTTP)."""

import json
from datetime import date, timedelta

import httpx
import pytest

from app import sla, warranty
from app.config import settings
from app.models import Device, DeviceWarranty
from tests.conftest import as_user

DELL_ANSWER = [
    {
        "id": 1, "serviceTag": "ABC1234", "shipDate": "2023-03-10T05:00:00Z", "productLineDescription": "LATITUDE 5440", "invalid": False,
        "entitlements": [
            {"serviceLevelDescription": "ProSupport", "entitlementType": "INITIAL", "startDate": "2023-03-10T05:00:00.000Z", "endDate": "2026-03-10T04:59:59.000Z"},
            {"serviceLevelDescription": "ProSupport Plus", "entitlementType": "EXTENDED", "startDate": "2026-03-10T05:00:00.000Z", "endDate": "2028-03-10T04:59:59.000Z"},
        ],
    },
    {"id": 2, "serviceTag": "NOPE999", "invalid": True, "entitlements": []},
]
LENOVO_ANSWER = [
    {
        "Serial": "PF3ABCDE", "Product": "ThinkPad T14 Gen 4", "InWarranty": True, "Purchased": "2024-01-15T00:00:00Z", "Shipped": "2024-01-02T00:00:00Z",
        "Warranty": [{"ID": "1EZ", "Name": "Base Warranty", "Type": "BASE", "Start": "2024-01-15T00:00:00Z", "End": "2027-01-14T00:00:00Z"}],
        "Contract": [{"Contract": "C1", "SLA": "Premier Support", "Start": "2024-01-15T00:00:00Z", "End": "2027-07-14T00:00:00Z"}],
    }
]


class Vendors:
    """Fake Dell and Lenovo APIs; records requests."""

    def __init__(self):
        self.calls = []
        self.dell_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url.startswith(warranty.DELL + "/auth/oauth/v2/token"):
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            assert body["grant_type"] == "client_credentials" and body["client_id"] == "dell-id"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if url.startswith(warranty.DELL + "/PROD/sbil/eapi/v5/asset-entitlements"):
            assert request.headers["Authorization"] == "Bearer tok"
            if self.dell_status != 200:
                return httpx.Response(self.dell_status)
            tags = request.url.params["servicetags"].split(",")
            return httpx.Response(200, json=[a for a in DELL_ANSWER if a["serviceTag"] in tags])
        if url == warranty.LENOVO:
            assert request.headers["ClientID"] == "len-token"
            serials = request.content.decode().split("=", 1)[1].replace("%2C", ",").split(",")
            return httpx.Response(200, json=[a for a in LENOVO_ANSWER if a["Serial"] in serials] or {"ErrorCode": 100, "ErrorMessage": "Warranty data not found", "Serial": serials[0]})
        return httpx.Response(404)


@pytest.fixture
def vendors(monkeypatch):
    monkeypatch.setattr(settings, "dell_client_id", "dell-id")
    monkeypatch.setattr(settings, "dell_client_secret", "dell-secret")
    monkeypatch.setattr(settings, "lenovo_client_id", "len-token")
    fake = Vendors()
    real = httpx.Client
    monkeypatch.setattr(warranty.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(fake)))
    return fake


def device(db, name, manufacturer, serial, **over):
    d = Device(name=name, manufacturer=manufacturer, serial=serial, **over)
    db.add(d)
    db.commit()
    return d


def test_dell_and_lenovo(client, db, agent, vendors):
    dell = device(db, "PC-0001", "Dell Inc.", "abc1234")
    unknown = device(db, "PC-0002", "Dell Inc.", "NOPE999")
    lenovo = device(db, "LT-0001", "LENOVO", "PF3ABCDE", warranty_ends=date(2026, 1, 1))
    device(db, "PC-0003", "HP", "5CG123")
    device(db, "PC-0004", "Dell Inc.", "")

    counts = warranty.run(db)
    db.commit()
    assert counts == {"looked_up": 3, "found": 2, "errors": 1, "problems": []}
    # Dell in one batch after one token.
    assert [str(c.url).split("?")[0] for c in vendors.calls] == [warranty.DELL + "/auth/oauth/v2/token", warranty.DELL + "/PROD/sbil/eapi/v5/asset-entitlements", warranty.LENOVO]

    # Empty dates filled from the maker; the latest entitlement wins.
    db.refresh(dell)
    assert (dell.warranty_ends, dell.purchased_on, dell.model) == (date(2028, 3, 10), date(2023, 3, 10), "LATITUDE 5440")
    w = client.get(f"/api/devices/{dell.id}/warranty", headers=as_user(agent)).json()
    assert (w["vendor"], w["available"], w["differs"], w["product"]) == ("dell", True, False, "LATITUDE 5440")
    assert w["entitlements"][1] == {"name": "ProSupport Plus", "type": "Extended", "start": "2026-03-10", "end": "2028-03-10"}
    assert client.get(f"/api/devices/{unknown.id}/warranty", headers=as_user(agent)).json()["error"] == "Dell doesn't know this service tag"

    # Lenovo: the contract runs longest; the device's own end differs, so it's offered, not applied.
    db.refresh(lenovo)
    assert (lenovo.warranty_ends, lenovo.purchased_on) == (date(2026, 1, 1), date(2024, 1, 15))
    w = client.get(f"/api/devices/{lenovo.id}/warranty", headers=as_user(agent)).json()
    assert (w["warranty_ends"], w["differs"], w["shipped_on"]) == ("2027-07-14", True, "2024-01-02")
    w = client.post(f"/api/devices/{lenovo.id}/warranty/apply", headers=as_user(agent)).json()
    db.refresh(lenovo)
    assert lenovo.warranty_ends == date(2027, 7, 14) and w["differs"] is False

    # Not due again until the recheck days pass, or the serial changes.
    assert warranty.due(db) == []
    dell.serial = "NEW0001"
    db.commit()
    assert [d.id for d in warranty.due(db)] == [dell.id]
    db.get(DeviceWarranty, lenovo.id).checked_at = sla.utcnow() - timedelta(days=31)
    db.commit()
    assert {d.id for d in warranty.due(db)} == {dell.id, lenovo.id}


def test_lookup_now_and_reasons(client, db, agent, vendors, monkeypatch):
    hp = device(db, "PC-0003", "HP", "5CG123")
    r = client.post(f"/api/devices/{hp.id}/warranty/lookup", headers=as_user(agent))
    assert r.status_code == 409 and "Only Dell and Lenovo" in r.json()["detail"]
    lenovo = device(db, "LT-0009", "Lenovo", "ZZZ")
    w = client.post(f"/api/devices/{lenovo.id}/warranty/lookup", headers=as_user(agent)).json()
    assert w["error"] == "Lenovo: Warranty data not found"
    dell = device(db, "PC-0001", "Dell Inc.", "ABC1234")
    vendors.dell_status = 403
    r = client.post(f"/api/devices/{dell.id}/warranty/lookup", headers=as_user(agent))
    assert r.status_code == 502 and "Warranty API is approved in TechDirect" in r.json()["detail"]
    # In the job, a refusal is reported and the other maker carries on.
    c = warranty.run(db)
    assert c["problems"] == ["Dell refused the lookup (403): check that the Warranty API is approved in TechDirect"]
    monkeypatch.setattr(settings, "dell_client_id", "")
    w = client.get(f"/api/devices/{dell.id}/warranty", headers=as_user(agent)).json()
    assert w["available"] is False and "Settings > Warranty lookups" in w["reason"]
    assert json.dumps(w)  # serialisable
