"""Warranty lookups with the makers' own APIs (read-only), for devices with a serial.

- Dell TechDirect: POST {DELL}/auth/oauth/v2/token (client credentials) gives a bearer token;
  GET {DELL}/PROD/sbil/eapi/v5/asset-entitlements?servicetags=A,B lists, per service tag, the
  ship date, product line and entitlements (serviceLevelDescription, entitlementType, startDate,
  endDate). Needs Dell's approval of the Warranty API in TechDirect.
- Lenovo: POST https://supportapi.lenovo.com/v2.5/warranty with header ClientID and a form body
  Serial=A,B gives, per serial, Product, Shipped, Purchased, InWarranty and Warranty / Contract
  lists with Start and End (supportapi.lenovo.com/Documentation/Warranty.html). The ClientID
  comes from Lenovo.

Only these documented APIs are used, with the customer's own credentials; nothing scrapes the
makers' support websites. What the maker says is kept in device_warranties; a device's own
purchase and warranty dates are filled when empty and otherwise left for a technician to update.
"""

import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import sla
from app.config import settings
from app.models import Device, DeviceWarranty

log = logging.getLogger(__name__)

DELL = "https://apigtwb2c.us.dell.com"
LENOVO = "https://supportapi.lenovo.com/v2.5/warranty"
BATCH = 100
# At most this many devices per run, to stay gentle with the makers' APIs.
PER_RUN = 1000


class WarrantyError(RuntimeError):
    """The message is safe to show."""


def vendor_of(manufacturer: str) -> str | None:
    m = (manufacturer or "").lower()
    if "dell" in m:
        return "dell"
    if "lenovo" in m:
        return "lenovo"
    return None


def enabled(vendor: str) -> bool:
    if vendor == "dell":
        return bool(settings.dell_client_id and settings.dell_client_secret)
    if vendor == "lenovo":
        return bool(settings.lenovo_client_id)
    return False


def _day(value) -> date | None:
    """2024-03-01T00:00:00Z, 2024-03-01T05:00:00.000+0000 or 2024-03-01."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


class Dell:
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self.http = http or httpx.Client(timeout=30)
        self.token: str | None = None

    def _token(self) -> str:
        if self.token is None:
            r = self.http.post(f"{DELL}/auth/oauth/v2/token", data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret})
            if r.status_code >= 400:
                raise WarrantyError(f"Dell refused the TechDirect client ID and secret ({r.status_code})")
            self.token = r.json().get("access_token")
            if not self.token:
                raise WarrantyError("Dell gave no access token")
        return self.token

    def lookup(self, serials: list[str]) -> dict[str, dict]:
        """{serial (upper case): result}; serials Dell doesn't know get an error."""
        r = self.http.get(f"{DELL}/PROD/sbil/eapi/v5/asset-entitlements", params={"servicetags": ",".join(serials)}, headers={"Authorization": f"Bearer {self._token()}"})
        if r.status_code in (401, 403):
            raise WarrantyError(f"Dell refused the lookup ({r.status_code}): check that the Warranty API is approved in TechDirect")
        if r.status_code >= 400:
            raise WarrantyError(f"Dell answered {r.status_code}")
        out = {}
        for a in r.json() or []:
            tag = str(a.get("serviceTag") or "").upper()
            if not tag:
                continue
            if a.get("invalid"):
                out[tag] = {"error": "Dell doesn't know this service tag"}
                continue
            ents = [
                {"name": e.get("serviceLevelDescription") or "", "type": (e.get("entitlementType") or "").title(), "start": _iso(_day(e.get("startDate"))), "end": _iso(_day(e.get("endDate")))}
                for e in a.get("entitlements") or []
                if isinstance(e, dict)
            ]
            out[tag] = {
                "product": a.get("productLineDescription") or a.get("systemDescription") or "", "shipped_on": _day(a.get("shipDate")), "purchased_on": None,
                "entitlements": ents,
            }
        return out


class Lenovo:
    def __init__(self, client_id: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.http = http or httpx.Client(timeout=30)

    def lookup(self, serials: list[str]) -> dict[str, dict]:
        r = self.http.post(LENOVO, data={"Serial": ",".join(serials)}, headers={"ClientID": self.client_id})
        if r.status_code in (401, 403):
            raise WarrantyError(f"Lenovo refused the ClientID ({r.status_code})")
        if r.status_code >= 400:
            raise WarrantyError(f"Lenovo answered {r.status_code}")
        body = r.json()
        out = {}
        for a in body if isinstance(body, list) else [body]:
            if not isinstance(a, dict):
                continue
            serial = str(a.get("Serial") or "").upper()
            if not serial:
                continue
            if a.get("ErrorCode") or (not a.get("Warranty") and not a.get("Shipped") and a.get("ErrorMessage")):
                out[serial] = {"error": f"Lenovo: {a.get('ErrorMessage') or 'no warranty data'}"}
                continue
            ents = [
                {"name": w.get("Name") or w.get("Description") or w.get("ID") or "", "type": (w.get("Type") or "").title(), "start": _iso(_day(w.get("Start"))), "end": _iso(_day(w.get("End")))}
                for w in a.get("Warranty") or []
                if isinstance(w, dict)
            ] + [
                {"name": c.get("SLA") or c.get("Contract") or "Contract", "type": "Contract", "start": _iso(_day(c.get("Start"))), "end": _iso(_day(c.get("End")))}
                for c in a.get("Contract") or []
                if isinstance(c, dict)
            ]
            out[serial] = {"product": a.get("Product") or "", "shipped_on": _day(a.get("Shipped")), "purchased_on": _day(a.get("Purchased")), "entitlements": ents}
        return out


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def clients(http: httpx.Client | None = None) -> dict:
    out = {}
    if enabled("dell"):
        out["dell"] = Dell(settings.dell_client_id, settings.dell_client_secret, http)
    if enabled("lenovo"):
        out["lenovo"] = Lenovo(settings.lenovo_client_id, http)
    return out


def _store(session: Session, device: Device, vendor: str, result: dict | None, at: datetime) -> DeviceWarranty:
    w = session.get(DeviceWarranty, device.id) or DeviceWarranty(device_id=device.id)
    session.add(w)
    w.vendor, w.serial, w.checked_at = vendor, device.serial.strip(), at
    if result is None or result.get("error"):
        w.error = (result or {}).get("error") or f"{vendor.title()} returned nothing for this serial"
        return w
    w.error = None
    w.product = str(result.get("product") or "")[:255]
    w.shipped_on, w.purchased_on = result.get("shipped_on"), result.get("purchased_on")
    w.entitlements = result.get("entitlements") or []
    ends = [_day(e.get("end")) for e in w.entitlements]
    w.warranty_ends = max((d for d in ends if d), default=None)
    # Fill the device's own dates when empty; a difference stays for a technician to take.
    if device.warranty_ends is None and w.warranty_ends:
        device.warranty_ends = w.warranty_ends
    bought = w.purchased_on or w.shipped_on
    if device.purchased_on is None and bought:
        device.purchased_on = bought
    if not device.model and w.product:
        device.model = w.product[:200]
    return w


def lookup(session: Session, devices: list[Device], http: httpx.Client | None = None, at: datetime | None = None, strict: bool = False) -> dict:
    """Look these devices up, maker by maker, in batches. Doesn't commit. Returns counts, and in
    "problems" why a maker refused (strict: raise that instead)."""
    at = at or sla.utcnow()
    made = clients(http)
    counts: dict = {"looked_up": 0, "found": 0, "errors": 0, "problems": []}
    by_vendor: dict[str, list[Device]] = {}
    for d in devices:
        v = vendor_of(d.manufacturer)
        if v and v in made and d.serial.strip():
            by_vendor.setdefault(v, []).append(d)
    for vendor, items in by_vendor.items():
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            try:
                answers = made[vendor].lookup([d.serial.strip().upper() for d in batch])
            except (WarrantyError, httpx.HTTPError, ValueError) as exc:
                problem = str(exc) if isinstance(exc, WarrantyError) else f"{vendor.title()} couldn't be reached: {exc}"
                if strict:
                    raise WarrantyError(problem) from None
                counts["problems"].append(problem)
                log.warning("Warranty lookup: %s", problem, extra={"source": "devices"})
                break
            for d in batch:
                w = _store(session, d, vendor, answers.get(d.serial.strip().upper()), at)
                counts["looked_up"] += 1
                counts["errors" if w.error else "found"] += 1
    return counts


def due(session: Session, now: datetime | None = None, limit: int = PER_RUN) -> list[Device]:
    """Active Dell and Lenovo devices with a serial, never looked up, looked up under another serial, or not for a while."""
    now = now or sla.utcnow()
    vendors = [v for v in ("dell", "lenovo") if enabled(v)]
    if not vendors:
        return []
    maker = or_(*[func.lower(Device.manufacturer).contains(v) for v in vendors])
    stale = now - timedelta(days=settings.warranty_recheck_days)
    stmt = (
        select(Device).outerjoin(DeviceWarranty, DeviceWarranty.device_id == Device.id)
        .where(Device.is_active, maker, func.trim(Device.serial) != "")
        .where(or_(DeviceWarranty.device_id.is_(None), DeviceWarranty.checked_at < stale, DeviceWarranty.serial != func.trim(Device.serial)))
        .order_by(DeviceWarranty.checked_at.asc().nulls_first(), Device.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def run(session: Session, http: httpx.Client | None = None) -> dict:
    """The worker job: look up what's due. Doesn't commit."""
    devices = due(session)
    if not devices:
        return {"looked_up": 0, "found": 0, "errors": 0, "problems": []}
    return lookup(session, devices, http)
