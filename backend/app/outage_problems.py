# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outage -> problem -> everyone: cluster concurrent device drops by site, switch or subnet,
offer one problem record with a draft service notice, suggest the problem on new tickets from
people at that site/switch/subnet, and track when the outage recovers so everyone affected can
be updated and resolved in one step."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import ipam, netdisco, sla
from app.api.tickets import visible
from app.changes import _subnet_label, _subnet_site, _ticket_footprint
from app.models import (
    IpAddress,
    MerakiClient,
    MerakiWebhookIncident,
    NetworkInventory,
    NetworkObservation,
    NetworkOutage,
    Problem,
    ProblemStatus,
    Subnet,
    Ticket,
    User,
)

CLUSTER_WINDOW = timedelta(minutes=30)
OPEN_PROBLEMS = (ProblemStatus.open, ProblemStatus.investigating, ProblemStatus.known_error)


@dataclass
class _OutageItem:
    key: str
    name: str
    is_down: bool
    recovered: bool
    down_since: datetime | None
    ticket_id: int | None
    switch: str = ""
    subnet: str = ""
    site: str = ""
    explicit_site: bool = False
    changed_at: datetime | None = None


@dataclass
class _ProblemScopes:
    switches: dict[str, str] = field(default_factory=dict)  # lower -> label ("Switch SW-1")
    subnets: dict[str, str] = field(default_factory=dict)   # lower -> label ("Subnet 10.0.20.0/24...")
    sites: dict[str, str] = field(default_factory=dict)     # lower -> label ("Site HQ")
    active_devices: list[str] = field(default_factory=list)
    recovered_devices: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.switches or self.subnets or self.sites)


def _collect_outage_items(session: Session) -> list[_OutageItem]:
    items: list[_OutageItem] = []
    outages = session.scalars(select(NetworkOutage).order_by(NetworkOutage.down_since.asc().nulls_last())).all()
    incidents = session.scalars(select(MerakiWebhookIncident).order_by(MerakiWebhookIncident.occurred_at.asc())).all()
    if not outages and not incidents:
        return items

    all_subnets = session.scalars(select(Subnet).options(selectinload(Subnet.vlan))).all()
    parsed_subnets = []
    for s in all_subnets:
        try:
            parsed_subnets.append((s, ipam.parse_network(str(s.network))))
        except ipam.IpamError:
            pass

    inv_map: dict[tuple[str, str, str], NetworkInventory] = {}
    macs: set[str] = set()
    for o in outages:
        inv = session.get(NetworkInventory, (o.provider, o.source_id, o.device_key))
        if inv is not None:
            inv_map[(o.provider, o.source_id, o.device_key)] = inv
            if inv.mac:
                macs.add(inv.mac)

    obs_uplink: dict[str, str] = {}
    obs_site: dict[str, str] = {}
    meraki_uplink: dict[str, str] = {}
    meraki_site: dict[str, str] = {}
    snmp_ports = netdisco.ports_for(session, macs) if macs else {}
    if macs:
        for ob in session.scalars(select(NetworkObservation).where(NetworkObservation.mac.in_(macs)).order_by(NetworkObservation.observed_at.desc())):
            if ob.uplink_name:
                obs_uplink.setdefault(ob.mac, ob.uplink_name)
            if ob.source_name:
                obs_site.setdefault(ob.mac, ob.source_name)
        for mc in session.scalars(select(MerakiClient).where(MerakiClient.mac.in_(macs)).order_by(MerakiClient.observed_at.desc())):
            if mc.switch_name:
                meraki_uplink.setdefault(mc.mac, mc.switch_name)
            if mc.network_name:
                meraki_site.setdefault(mc.mac, mc.network_name)

    for o in outages:
        inv = inv_map.get((o.provider, o.source_id, o.device_key))
        name = (inv.name if inv and inv.name else "") or (inv.mac if inv and inv.mac else o.device_key)
        inv_online = inv is not None and inv.state == "online"
        recovered = (not o.is_down) or inv_online
        is_down = bool(o.is_down and not inv_online)

        switch = ""
        if inv is not None and inv.mac:
            switch = (
                obs_uplink.get(inv.mac)
                or meraki_uplink.get(inv.mac)
                or (snmp_ports.get(inv.mac, {}).get("switch") or "")
            )
        if not switch and inv is not None and inv.kind == "switch" and inv.name:
            switch = inv.name

        subnet_label = ""
        subnet_site = ""
        addr_str = inv.address if inv and inv.address else ""
        if not addr_str and inv and inv.mac:
            ip_row = session.scalar(select(IpAddress).where(IpAddress.mac == inv.mac).limit(1))
            if ip_row is not None:
                addr_str = str(ip_row.address)
        if addr_str:
            try:
                addr = ipam.parse_address(addr_str)
                hit = next((s for s, net in parsed_subnets if addr in net), None)
                if hit is not None:
                    subnet_label = _subnet_label(hit)
                    subnet_site = _subnet_site(hit)
            except ipam.IpamError:
                pass

        explicit_site = bool((inv and inv.source_name) or (inv and inv.mac and (obs_site.get(inv.mac) or meraki_site.get(inv.mac))))
        site = (
            (inv.source_name if inv and inv.source_name else "")
            or (obs_site.get(inv.mac, "") if inv and inv.mac else "")
            or (meraki_site.get(inv.mac, "") if inv and inv.mac else "")
            or subnet_site
        )

        items.append(
            _OutageItem(
                key=f"{o.provider}:{o.source_id}:{o.device_key}",
                name=name,
                is_down=is_down,
                recovered=recovered,
                down_since=o.down_since or o.changed_at,
                ticket_id=o.ticket_id,
                switch=switch,
                subnet=subnet_label,
                site=site,
                explicit_site=explicit_site,
                changed_at=o.changed_at,
            )
        )

    if incidents:
        serials = {inc.serial.lower() for inc in incidents if inc.serial}
        net_ids = {inc.network_id for inc in incidents if inc.network_id}
        serial_names: dict[str, str] = {}
        net_names: dict[str, str] = {}
        if serials or net_ids:
            for mc in session.scalars(
                select(MerakiClient).where(
                    or_(
                        func.lower(MerakiClient.switch_serial).in_(serials or {""}),
                        MerakiClient.network_id.in_(net_ids or {""}),
                    )
                )
            ):
                if mc.switch_serial and mc.switch_name:
                    serial_names.setdefault(mc.switch_serial.lower(), mc.switch_name)
                if mc.network_id and mc.network_name:
                    net_names.setdefault(mc.network_id, mc.network_name)
        for inc in incidents:
            sw_name = serial_names.get(inc.serial.lower(), inc.serial)
            site_name = net_names.get(inc.network_id, inc.network_id)
            items.append(
                _OutageItem(
                    key=f"meraki:{inc.network_id}:{inc.serial}",
                    name=sw_name,
                    is_down=bool(inc.is_down),
                    recovered=not inc.is_down,
                    down_since=inc.occurred_at,
                    ticket_id=inc.ticket_id,
                    switch=sw_name,
                    subnet="",
                    site=site_name,
                    explicit_site=bool(site_name),
                    changed_at=inc.received_at or inc.occurred_at,
                )
            )

    return items


def _within_window(group: list[_OutageItem]) -> list[_OutageItem]:
    """Return the largest subset of items that dropped within CLUSTER_WINDOW of one another."""
    if len(group) < 2:
        return []
    with_ts = sorted([i for i in group if i.down_since is not None], key=lambda i: i.down_since)  # type: ignore[arg-type, return-value]
    without_ts = [i for i in group if i.down_since is None]
    if len(with_ts) <= 1:
        return group if len(with_ts) + len(without_ts) >= 2 else []
    best: list[_OutageItem] = []
    for idx, start_item in enumerate(with_ts):
        limit = start_item.down_since + CLUSTER_WINDOW  # type: ignore[operator]
        window_items = [other for other in with_ts[idx:] if other.down_since <= limit] + without_ts  # type: ignore[operator]
        if len(window_items) > len(best):
            best = window_items
    return best if len(best) >= 2 else []


def _parse_scope_into(raw: str, scopes: _ProblemScopes) -> None:
    text = (raw or "").strip()
    if not text:
        return
    low = text.lower()
    if low.startswith("switch "):
        name = text[7:].strip()
        if name:
            scopes.switches.setdefault(name.lower(), f"Switch {name}")
    elif low.startswith("subnet "):
        name = text[7:].strip()
        if name:
            scopes.subnets.setdefault(name.lower(), f"Subnet {name}")
    elif low.startswith("site "):
        name = text[5:].strip()
        if name:
            scopes.sites.setdefault(name.lower(), f"Site {name}")
    else:
        scopes.sites.setdefault(low, f"Site {text}")


def _item_matches_scopes(item: _OutageItem, scopes: _ProblemScopes) -> bool:
    if item.switch and item.switch.lower() in scopes.switches:
        return True
    if item.subnet and item.subnet.lower() in scopes.subnets:
        return True
    if item.site and item.site.lower() in scopes.sites:
        return True
    return False


def _problem_scopes(problem: Problem, all_items: list[_OutageItem]) -> _ProblemScopes:
    scopes = _ProblemScopes()
    _parse_scope_into(problem.outage_scope, scopes)
    pt_ids = {t.id for t in problem.tickets}
    matched_items: list[_OutageItem] = []
    seen_keys: set[str] = set()
    linked_keys: set[str] = set()

    # First pass: items linked by ticket_id expand the problem's known outage scopes.
    for item in all_items:
        if item.ticket_id and item.ticket_id in pt_ids:
            matched_items.append(item)
            seen_keys.add(item.key)
            linked_keys.add(item.key)
            if item.switch:
                scopes.switches.setdefault(item.switch.lower(), f"Switch {item.switch}")
            if item.subnet:
                scopes.subnets.setdefault(item.subnet.lower(), f"Subnet {item.subnet}")
            if item.site:
                scopes.sites.setdefault(item.site.lower(), f"Site {item.site}")

    # Second pass: items matching the problem's outage scope or the scopes of its linked
    # outages. Matches don't widen the scope further, so one site can't chain to the next.
    if not scopes.is_empty():
        for item in all_items:
            if item.key not in seen_keys and _item_matches_scopes(item, scopes):
                matched_items.append(item)
                seen_keys.add(item.key)

    # A recovery only counts if it happened around or after this problem: an outage at the
    # same site months ago isn't part of it.
    since = problem.created_at - CLUSTER_WINDOW if problem.created_at else None
    for item in matched_items:
        if item.is_down and not item.recovered:
            if item.name not in scopes.active_devices:
                scopes.active_devices.append(item.name)
        elif item.recovered and (since is None or item.key in linked_keys or (item.changed_at is not None and item.changed_at >= since)):
            if item.name not in scopes.recovered_devices:
                scopes.recovered_devices.append(item.name)

    return scopes


def outage_clusters(session: Session, user: User) -> list[dict]:
    """Active multi-device outage clusters (2+ devices down at the same switch, subnet or site
    within CLUSTER_WINDOW) that don't already have an open problem record."""
    all_items = _collect_outage_items(session)
    down_items = [i for i in all_items if i.is_down and not i.recovered]
    if len(down_items) < 2:
        return []

    open_problems = session.scalars(
        select(Problem).where(Problem.status.in_(OPEN_PROBLEMS)).options(selectinload(Problem.tickets))
    ).all()
    covered_switches: set[str] = set()
    covered_subnets: set[str] = set()
    covered_sites: set[str] = set()
    covered_tickets: set[int] = set()
    for p in open_problems:
        ps = _problem_scopes(p, all_items)
        covered_switches.update(ps.switches)
        covered_subnets.update(ps.subnets)
        covered_sites.update(ps.sites)
        covered_tickets.update(t.id for t in p.tickets)

    by_switch: dict[str, tuple[str, list[_OutageItem]]] = {}
    by_subnet: dict[str, tuple[str, list[_OutageItem]]] = {}
    by_site: dict[str, tuple[str, list[_OutageItem]]] = {}
    for item in down_items:
        if item.switch:
            k = item.switch.lower()
            by_switch.setdefault(k, (item.switch, []))[1].append(item)
        if item.subnet:
            k = item.subnet.lower()
            by_subnet.setdefault(k, (item.subnet, []))[1].append(item)
        if item.site:
            k = item.site.lower()
            by_site.setdefault(k, (item.site, []))[1].append(item)

    candidates: list[tuple[int, str, str, str, list[_OutageItem]]] = []
    for k, (label, group) in by_switch.items():
        if k in covered_switches:
            continue
        clustered = _within_window(group)
        if clustered:
            candidates.append((1, f"switch:{k}", "switch", f"Switch {label}", clustered))
    for k, (label, group) in by_site.items():
        if k in covered_sites:
            continue
        clustered = _within_window(group)
        if clustered:
            rank = 2 if any(i.explicit_site for i in clustered) else 4
            candidates.append((rank, f"site:{k}", "site", f"Site {label}", clustered))
    for k, (label, group) in by_subnet.items():
        if k in covered_subnets:
            continue
        clustered = _within_window(group)
        if clustered:
            candidates.append((3, f"subnet:{k}", "subnet", f"Subnet {label}", clustered))

    # Deduplicate clusters covering the exact same set of down devices, keeping the highest-ranked scope.
    candidates.sort(key=lambda c: (-len(c[4]), c[0], c[1]))
    seen_device_sets: set[frozenset[str]] = set()
    out: list[dict] = []

    all_tids = {i.ticket_id for _, _, _, _, grp in candidates for i in grp if i.ticket_id}
    visible_tids: set[int] = set()
    if all_tids:
        visible_tids = set(session.scalars(visible(select(Ticket.id).where(Ticket.id.in_(all_tids)), user)))

    for _, key, kind, scope_label, clustered in candidates:
        dev_set = frozenset(i.key for i in clustered)
        if dev_set in seen_device_sets:
            continue
        tids = sorted({i.ticket_id for i in clustered if i.ticket_id and i.ticket_id in visible_tids})
        if tids and all(tid in covered_tickets for tid in tids):
            continue
        seen_device_sets.add(dev_set)
        devices = list(dict.fromkeys(i.name for i in clustered))
        times = [i.down_since for i in clustered if i.down_since is not None]
        down_since = min(times) if times else None
        dev_summary = ", ".join(devices[:3]) + (f" +{len(devices) - 3} more" if len(devices) > 3 else "")
        out.append({
            "key": key,
            "scope_kind": kind,
            "scope_label": scope_label,
            "devices": devices,
            "down_since": down_since,
            "ticket_ids": tids,
            "suggested_title": f"Outage at {scope_label} ({dev_summary})",
            "suggested_notice_title": f"Service outage: {scope_label}",
            "suggested_notice_message": f"We are investigating a network outage affecting {scope_label} ({dev_summary}).",
        })
    return out


def _ticket_matches_problem_scopes(session: Session, ticket: Ticket, scopes: _ProblemScopes, all_items: list[_OutageItem] | None = None) -> str:
    if scopes.is_empty():
        return ""
    if all_items:
        for item in all_items:
            if item.ticket_id == ticket.id:
                if item.switch and item.switch.lower() in scopes.switches:
                    return f"Part of this outage · {scopes.switches[item.switch.lower()]}"
                if item.subnet and item.subnet.lower() in scopes.subnets:
                    return f"Part of this outage · {scopes.subnets[item.subnet.lower()]}"
                if item.site and item.site.lower() in scopes.sites:
                    return f"Part of this outage · {scopes.sites[item.site.lower()]}"
    fp = _ticket_footprint(session, ticket)
    for sw_key in fp.switches:
        if sw_key in scopes.switches:
            return f"Part of this outage · {scopes.switches[sw_key]}"
    for dev_low in fp.device_by_lower:
        if dev_low in scopes.switches:
            return f"Part of this outage · {scopes.switches[dev_low]}"
    for sub_label in fp.subnets.values():
        if sub_label.lower() in scopes.subnets:
            return f"Part of this outage · {scopes.subnets[sub_label.lower()]}"
    for site_key in fp.sites:
        if site_key in scopes.sites:
            return f"Part of this outage · {scopes.sites[site_key]}"
    if fp.is_empty() and ticket.requester and ticket.requester.office:
        off = ticket.requester.office.strip().lower()
        if off in scopes.sites:
            return f"Part of this outage · {scopes.sites[off]}"
    return ""


def problem_outage_details(session: Session, problem: Problem, user: User) -> dict:
    """Outage status, recovery flag and unlinked tickets at the same site/switch/subnet for a problem."""
    all_items = _collect_outage_items(session)
    scopes = _problem_scopes(problem, all_items)
    linked_ids = {t.id for t in problem.tickets}
    open_linked = [t for t in problem.tickets if t.status not in sla.CLOSED_STATUSES]
    any_recovery_review = any(bool(getattr(t, "recovery_review", False)) for t in open_linked)

    outage_recovered = bool(
        problem.status in OPEN_PROBLEMS
        and not scopes.active_devices
        and (scopes.recovered_devices or any_recovery_review)
    )

    suggested_tickets = []
    if problem.status in OPEN_PROBLEMS and not scopes.is_empty():
        stmt = (
            visible(select(Ticket).where(Ticket.status.not_in(sla.CLOSED_STATUSES)), user)
            .options(selectinload(Ticket.computer), selectinload(Ticket.requester))
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(100)
        )
        for t in session.scalars(stmt):
            if t.id in linked_ids:
                continue
            scope = _ticket_matches_problem_scopes(session, t, scopes, all_items)
            if scope:
                suggested_tickets.append({"ticket": t, "scope": scope})

    return {
        "active_outage_devices": scopes.active_devices,
        "recovered_outage_devices": scopes.recovered_devices,
        "outage_recovered": outage_recovered,
        "suggested_tickets": suggested_tickets[:20],
    }


def suggested_problems_for_ticket(session: Session, ticket: Ticket, user: User, can_see_fn) -> list[ tuple[Problem, str] ]:
    """Open outage problems at the same site, switch or subnet as this ticket that aren't linked yet."""
    all_items = _collect_outage_items(session)
    open_problems = session.scalars(
        select(Problem)
        .where(Problem.status.in_(OPEN_PROBLEMS))
        .options(selectinload(Problem.owner), selectinload(Problem.tickets))
        .order_by(Problem.updated_at.desc(), Problem.id.desc())
    ).all()
    if not open_problems:
        return []
    out: list[tuple[Problem, str]] = []
    for p in open_problems:
        if any(t.id == ticket.id for t in p.tickets):
            continue
        if not can_see_fn(session, p, user):
            continue
        scopes = _problem_scopes(p, all_items)
        if scopes.is_empty():
            continue
        scope = _ticket_matches_problem_scopes(session, ticket, scopes, all_items)
        if scope:
            out.append((p, scope))
    return out
