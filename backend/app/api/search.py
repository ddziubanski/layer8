# SPDX-License-Identifier: AGPL-3.0-or-later
"""One search for everything (Ctrl K): devices, people, problems, changes, knowledge base
articles, purchase orders, vendors and systems, grouped by type. Tickets keep their own search
(GET /api/tickets?q=), which the palette already uses. Each type applies its usual rules: the
same staff-only access as its own list, problems only when every linked ticket is visible, and
modules switched off in Settings > Modules are left out. Identifiers match exactly or by prefix
(IP addresses, MACs, numbers such as PRB-00012 or PO-00012); words match names and titles."""
import ipaddress
import re
from urllib.parse import quote

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app import changes as change_lib, ipam, kb, purchasing, workspace
from app.api.problems import _can_see as can_see_problem, _label as problem_label
from app.auth import Agent, SessionDep
from app.models import Change, Device, IpAddress, KbArticle, KbStatus, Problem, PurchaseOrder, System, User, Vendor

router = APIRouter(prefix="/api/search", tags=["search"])

PER_TYPE = 5


class SearchHit(BaseModel):
    kind: str
    id: int
    label: str
    detail: str = ""
    to: str


class SearchGroup(BaseModel):
    kind: str
    label: str
    hits: list[SearchHit]


class SearchOut(BaseModel):
    q: str
    groups: list[SearchGroup]


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _number(text: str) -> int | None:
    """12, #12, PRB-00012, CHG-2026-0012…: the trailing number, if the text is just an identifier."""
    m = re.fullmatch(r"#?(?:[A-Za-z]{2,5}[-\s]?)?(?:\d{4}-)?0*(\d{1,9})", text.strip())
    return int(m.group(1)) if m else None


def _mac(text: str) -> str | None:
    try:
        return ipam.normalize_mac(text) or None
    except ipam.IpamError:
        return None


def _ip_prefix(text: str) -> str | None:
    """A whole or partial IPv4/IPv6 address ("10.20.", "10.20.0.5", "fe80::1")."""
    t = text.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{0,3}){1,3}", t) or (":" in t and re.fullmatch(r"[0-9a-fA-F:]+", t) and _mac(t) is None):
        return t
    try:
        return str(ipaddress.ip_address(t))
    except ValueError:
        return None


def _devices(session, q: str) -> list[SearchHit]:
    like = _like(q)
    hits: dict[int, SearchHit] = {}
    ip, mac = _ip_prefix(q), _mac(q)
    if ip or mac:
        cond = IpAddress.mac == mac if mac else func.host(IpAddress.address).like(ip + "%")
        for rec in session.scalars(select(IpAddress).where(cond).order_by(IpAddress.address).limit(PER_TYPE * 2)):
            device = session.get(Device, rec.device_id) if rec.device_id else None
            where = " · ".join(x for x in (str(rec.address).split("/")[0], rec.mac or "") if x)
            if device is not None:
                hits.setdefault(device.id, SearchHit(kind="device", id=device.id, label=device.name, detail=where, to=f"/devices/{device.id}"))
            else:
                # An address with no device yet: open it in IPAM.
                hits.setdefault(-rec.id, SearchHit(kind="device", id=rec.id, label=rec.hostname or str(rec.address).split("/")[0],
                                                    detail=f"IP record · {where}", to=f"/ipam/addresses?q={str(rec.address).split('/')[0]}"))
    stmt = (select(Device).where(or_(Device.name.ilike(like), Device.dns_hostname.ilike(like), Device.serial.ilike(like), Device.model.ilike(like)))
            .order_by(Device.is_active.desc(), func.length(Device.name), Device.name).limit(PER_TYPE))
    for d in session.scalars(stmt):
        detail = " · ".join(x for x in (d.os or "", f"serial {d.serial}" if d.serial and q.lower() in d.serial.lower() else "", "" if d.is_active else "retired") if x)
        hits.setdefault(d.id, SearchHit(kind="device", id=d.id, label=d.name, detail=detail, to=f"/devices/{d.id}"))
    return list(hits.values())[:PER_TYPE]


def _people(session, q: str) -> list[SearchHit]:
    like = _like(q)
    stmt = (select(User).where(or_(User.display_name.ilike(like), User.email.ilike(like), User.upn.ilike(like), User.sam_account_name.ilike(like)))
            .order_by(User.is_active.desc(), func.length(User.display_name), User.display_name).limit(PER_TYPE))
    return [SearchHit(kind="person", id=u.id, label=u.display_name, detail=u.email + ("" if u.is_active else " · inactive"), to=f"/people/{u.id}")
            for u in session.scalars(stmt)]


def _problems(session, q: str, user: User) -> list[SearchHit]:
    n = _number(q)
    cond = Problem.title.ilike(_like(q)) | Problem.workaround.ilike(_like(q))
    if n is not None:
        cond = cond | (Problem.id == n)
    out = []
    for p in session.scalars(select(Problem).where(cond).order_by((Problem.id == (n or 0)).desc(), Problem.updated_at.desc()).limit(PER_TYPE * 3)):
        if can_see_problem(session, p, user):
            out.append(SearchHit(kind="problem", id=p.id, label=f"{problem_label(p)} {p.title}", detail=p.status.value.replace("_", " "), to=f"/problems/{p.id}"))
        if len(out) == PER_TYPE:
            break
    return out


def _changes(session, q: str) -> list[SearchHit]:
    n = _number(q)
    cond = Change.title.ilike(_like(q))
    if n is not None:
        cond = cond | (Change.id == n)
    stmt = select(Change).where(cond).order_by((Change.id == (n or 0)).desc(), Change.created_at.desc()).limit(PER_TYPE)
    return [SearchHit(kind="change", id=c.id, label=f"{change_lib.label(c)} {c.title}", detail=c.status.value.replace("_", " "), to=f"/changes/{c.id}")
            for c in session.scalars(stmt)]


def _articles(session, q: str, user: User) -> list[SearchHit]:
    found = kb.articles(session, user, q, limit=PER_TYPE)
    if len(found) < PER_TYPE:
        # Full-text needs whole words; a title fragment ("vpn cl") still finds it.
        seen = {a.id for a in found}
        more = session.scalars(select(KbArticle).where(KbArticle.title.ilike(_like(q)), KbArticle.status != KbStatus.archived)
                               .order_by(KbArticle.updated_at.desc()).limit(PER_TYPE))
        found += [a for a in more if a.id not in seen][: PER_TYPE - len(found)]
    return [SearchHit(kind="article", id=a.id, label=a.title, detail=" · ".join(x for x in (a.category or "", a.status.value) if x), to=f"/kb/{a.id}")
            for a in found]


def _purchase_orders(session, q: str) -> list[SearchHit]:
    po = purchasing.find_by_number(session, q)
    like = _like(q)
    stmt = (select(PurchaseOrder).outerjoin(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .where(or_(PurchaseOrder.title.ilike(like), PurchaseOrder.vendor_reference.ilike(like), Vendor.name.ilike(like)))
            .order_by(PurchaseOrder.created_at.desc()).limit(PER_TYPE))
    rows = ([po] if po is not None else []) + [p for p in session.scalars(stmt) if po is None or p.id != po.id]
    return [SearchHit(kind="purchase_order", id=p.id, label=f"{purchasing.label(p)} {p.title}", detail=p.status.value.replace("_", " "), to=f"/purchase-orders/{p.id}")
            for p in rows[:PER_TYPE]]


def _vendors(session, q: str) -> list[SearchHit]:
    like = _like(q)
    vendors = session.scalars(select(Vendor).where(or_(Vendor.name.ilike(like), Vendor.account_number.ilike(like)))
                              .order_by(Vendor.is_active.desc(), Vendor.name).limit(PER_TYPE))
    return [SearchHit(kind="vendor", id=v.id, label=v.name, detail=v.support_phone or v.website or "", to=f"/vendors?q={quote(v.name)}") for v in vendors]


def _systems(session, q: str) -> list[SearchHit]:
    systems = session.scalars(select(System).where(System.name.ilike(_like(q))).order_by(System.is_active.desc(), System.name).limit(PER_TYPE))
    return [SearchHit(kind="system", id=s.id, label=s.name, detail=(s.description or "")[:80], to=f"/vendors/systems?q={quote(s.name)}") for s in systems]


@router.get("", response_model=SearchOut)
def search_everything(user: Agent, session: SessionDep, q: str = Query(min_length=2, max_length=200)):
    q = q.strip()
    off = set(workspace.disabled())
    groups: list[tuple[str, str, str, object]] = [
        ("device", "Devices", "devices", lambda: _devices(session, q)),
        ("person", "People", "", lambda: _people(session, q)),
        ("problem", "Problems", "problems", lambda: _problems(session, q, user)),
        ("change", "Changes", "changes", lambda: _changes(session, q)),
        ("article", "Knowledge base", "kb", lambda: _articles(session, q, user)),
        ("purchase_order", "Purchase orders", "purchase-orders", lambda: _purchase_orders(session, q)),
        ("vendor", "Vendors", "vendors", lambda: _vendors(session, q)),
        ("system", "Systems", "vendors", lambda: _systems(session, q)),
    ]
    out = []
    for kind, label, module, find in groups:
        if module and module in off:
            continue
        hits = find()
        if hits:
            out.append(SearchGroup(kind=kind, label=label, hits=hits))
    return SearchOut(q=q, groups=out)
