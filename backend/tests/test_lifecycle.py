"""Device lifecycle: warranty and replacement dates, who may set them, the report and its forecast."""

from datetime import date, timedelta

from app import lifecycle
from app.models import Device, DeviceSource
from tests.conftest import as_user

TODAY = date(2026, 10, 21)


def test_technicians_keep_lifecycle_details_even_on_ad_computers(client, db, agent):
    pc = Device(name="WS-0142", source=DeviceSource.directory, directory_id="g1")
    db.add(pc)
    db.commit()
    body = {"manufacturer": "Dell", "model": "OptiPlex 7010", "serial": "ABC1234", "purchased_on": "2022-03-01", "warranty_ends": "2025-03-01"}
    r = client.patch(f"/api/devices/{pc.id}", json=body, headers=as_user(agent))
    assert r.status_code == 200, r.text
    assert (r.json()["model"], r.json()["warranty_ends"]) == ("OptiPlex 7010", "2025-03-01")
    # Other fields stay admin-only, and AD's stay AD's.
    assert client.patch(f"/api/devices/{pc.id}", json={"description": "x"}, headers=as_user(agent)).status_code == 403


def test_report_and_forecast(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "device_life_years", 4)
    monkeypatch.setattr(settings, "warranty_warn_days", 60)
    db.add_all([
        Device(name="SOON", warranty_ends=TODAY + timedelta(days=30), purchased_on=date(2023, 1, 10)),  # due 2027 Q1
        Device(name="ENDED", warranty_ends=TODAY - timedelta(days=1), purchased_on=date(2021, 5, 1)),  # due 2025: overdue
        Device(name="OWN-DATE", purchased_on=date(2025, 1, 1), replace_by=date(2026, 12, 15)),  # its own date: this quarter
        Device(name="LEAP", purchased_on=date(2024, 2, 29)),  # 2028-02-28
        Device(name="UNKNOWN"),
        Device(name="GONE", purchased_on=date(2020, 1, 1), is_active=False),
    ])
    db.commit()
    r = lifecycle.report(db, TODAY)
    assert [d.name for d in r.warranty_soon] == ["SOON"] and [d.name for d in r.warranty_ended] == ["ENDED"]
    assert [(d.name, due) for d, due in r.replace_soon] == [("ENDED", date(2025, 5, 1)), ("OWN-DATE", date(2026, 12, 15)), ("SOON", date(2027, 1, 10))]
    assert r.forecast[0] == ("2026 Q4", 1) and dict(r.forecast)["2027 Q1"] == 1 and dict(r.forecast)["2028 Q1"] == 1
    assert len(r.forecast) == 12 and r.forecast[-1][0] == "2029 Q3"
    assert (r.overdue, r.unknown) == (1, 1)


def test_lifecycle_api(client, db, agent):
    db.add(Device(name="SOON", warranty_ends=date.today() + timedelta(days=10)))
    db.commit()
    r = client.get("/api/devices/lifecycle", headers=as_user(agent)).json()
    assert [d["name"] for d in r["warranty_soon"]] == ["SOON"] and r["life_years"] == 5 and len(r["forecast"]) == 12
