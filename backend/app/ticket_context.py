# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket context: what's around this ticket right now, from cached data only.

Before anyone starts guessing on a ticket, show:
- whether the requester's AD account is locked, disabled or expiring;
- the requester's or the linked device's live state: online, last seen, switch port, pending reboot, critical patches;
- any open outage, problem or recent change touching that device, its switch, its subnet or its site.

No vendor calls: everything comes from the local tables the syncs already keep current.
Visibility rules apply as usual: outage tickets and problems in queues the viewer can't see are
left out (or counted in `hidden_problems`, never named).
"""
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import ipam, netdisco, network, numbering, sla
from app.api.problems import _can_see as can_see_problem, _label as problem_label
from app.api.tickets import visible
from app.config import settings
from app.models import (
    AuthSource,
    Change,
    ChangeStatus,
    Device,
    DevicePatch,
    DeviceSource,
    IpAddress,
    MerakiClient,
    MerakiWebhookIncident,
    NetworkDevice,
    NetworkInventory,
    NetworkObservation,
    NetworkOutage,
    Problem,
    ProblemStatus,
    Subnet,
    SwitchPortMac,
    System,
    Ticket,
    TicketDevice,
    TicketSystem,
    User,
    change_devices,
    change_systems,
    change_tickets,
    problem_tickets,
)

EXPIRING_DAYS = 14
RECENT_CHANGE_DAYS = 7
UPCOMING_CHANGE_HOURS = 24
OPEN_PROBLEMS = (ProblemStatus.open, ProblemStatus.investigating, ProblemStatus.known_error)
RELEVANT_CHANGES = (
    ChangeStatus.in_progress,
    ChangeStatus.approved,
    ChangeStatus.completed,
    ChangeStatus.failed,
    ChangeStatus.rolled_back,
)


def _device_ips(session, d: Device) -> list[IpAddress]:
    """Addresses linked to the device, or unlinked ones whose host name matches it."""
    names = {n.lower() for n in (d.name, d.dns_hostname) if n}
    short = d.name.lower()
    match = or_(func.lower(IpAddress.hostname).in_(names), func.split_part(func.lower(IpAddress.hostname), ".", 1) == short)
    stmt = select(IpAddress).where(
        or_(IpAddress.device_id == d.id, IpAddress.device_id.is_(None) & (IpAddress.hostname != "") & match)
    ).order_by(IpAddress.address)
    return list(session.scalars(stmt))


def _subnet_site(s: Subnet | None) -> str:
    if s is None:
        return ""
    if s.site:
        return s.site
    return s.vlan.site if s.vlan and s.vlan.site else ""


def _subnet_label(s: Subnet) -> str:
    return f"{s.network} ({s.name})" if s.name else str(s.network)


def _where_obs(o: NetworkObservation) -> str:
    place = f"{o.uplink_name} port {o.port}" if o.uplink_name and o.port else (o.uplink_name or (f"Wi-Fi {o.ssid}" if o.ssid else ""))
    return " · ".join(x for x in (place, o.network) if x)


def _where_meraki(m: MerakiClient) -> str:
    place = f"{m.switch_name} port {m.port}" if m.switch_name and m.port else (m.switch_name or (f"Wi-Fi {m.ssid}" if m.ssid else ""))
    vlan = f"VLAN {m.vlan}" if m.vlan else ""
    return " · ".join(x for x in (place, vlan) if x)


def _where_snmp(p: dict) -> str:
    place = f"{p['switch']} port {p['port']}" if p.get("switch") and p.get("port") else (p.get("switch") or "")
    vlan = f"VLAN {p['vlan']}" if p.get("vlan") is not None else ""
    return " · ".join(x for x in (place, vlan) if x)


def _visible_ticket_label(session, ticket_id: int | None, user: User) -> tuple[int | None, str | None]:
    if not ticket_id:
        return None, None
    row = session.execute(visible(select(Ticket.id, Ticket.created_at).where(Ticket.id == ticket_id), user)).first()
    if row is None:
        return None, None
    return row[0], numbering.label(row[0], row[1])


def build(session, ticket: Ticket, user: User) -> dict:
    now = sla.utcnow()
    req = ticket.requester

    # 1. Requester's AD / sign-in account state.
    account = []
    if not req.is_active:
        account.append({
            "kind": "disabled",
            "label": "Disabled in Active Directory" if req.auth_source == AuthSource.directory else "Account is disabled",
            "tone": "bad",
            "at": None,
        })
    if req.ad_locked:
        account.append({"kind": "locked", "label": "Locked in Active Directory", "tone": "bad", "at": None})
    elif req.locked_until and req.locked_until > now:
        account.append({"kind": "locked", "label": "Sign-in locked after failed attempts", "tone": "warn", "at": req.locked_until})
    if req.account_expires_at is not None:
        if req.account_expires_at <= now:
            account.append({"kind": "expired", "label": "Active Directory account expired", "tone": "bad", "at": req.account_expires_at})
        elif req.account_expires_at <= now + timedelta(days=EXPIRING_DAYS):
            account.append({"kind": "expiring", "label": "Active Directory account expires soon", "tone": "warn", "at": req.account_expires_at})

    # 2. Devices around this ticket: its computer, linked devices, and (when no computer is set) the requester's assigned ones.
    picked: list[tuple[Device, str]] = []
    seen_devices: set[int] = set()
    if ticket.computer is not None:
        picked.append((ticket.computer, "computer"))
        seen_devices.add(ticket.computer.id)
    for d in session.scalars(
        select(Device).join(TicketDevice, TicketDevice.device_id == Device.id)
        .where(TicketDevice.ticket_id == ticket.id).order_by(Device.name)
    ):
        if d.id not in seen_devices:
            picked.append((d, "linked"))
            seen_devices.add(d.id)
    if ticket.computer_id is None:
        for d in session.scalars(
            select(Device).where(Device.assigned_user_id == req.id, Device.is_active.is_(True))
            .order_by(Device.last_logon_at.desc().nulls_last(), Device.name).limit(3)
        ):
            if d.id not in seen_devices:
                picked.append((d, "requester"))
                seen_devices.add(d.id)

    device_names = {d.id: d.name for d, _ in picked}
    system_names = {
        s.id: s.name
        for s in session.scalars(select(System).join(TicketSystem, TicketSystem.system_id == System.id).where(TicketSystem.ticket_id == ticket.id))
    }

    # Cached state per device, plus the network footprint (MACs, switches, subnets, sites).
    mac_to_device: dict[str, Device] = {}
    device_ips: dict[int, list[IpAddress]] = {}
    subnets: dict[int, Subnet] = {}
    sites: dict[str, str] = {}  # lower -> display
    switches: dict[str, str] = {}  # lower name/key -> display
    meraki_serials: dict[str, str] = {}  # lower serial -> display switch name or serial
    meraki_networks: dict[str, str] = {}  # network_id -> network_name
    snmp_switch_ids: dict[int, str] = {}  # network_device_id -> switch name
    source_sites: dict[tuple[str, str], str] = {}  # (provider, source_id) -> source_name

    for d, _ in picked:
        ips = _device_ips(session, d)
        device_ips[d.id] = ips
        for ip in ips:
            if ip.mac:
                mac_to_device.setdefault(ip.mac, d)
            if ip.system_id and ip.system_id not in system_names and ip.system is not None:
                system_names[ip.system_id] = ip.system.name
            s = ipam.subnet_of(session, ip.address)
            if s is not None:
                subnets[s.id] = s
                site = _subnet_site(s)
                if site:
                    sites.setdefault(site.lower(), site)

    all_macs = set(mac_to_device)
    obs_by_mac: dict[str, NetworkObservation] = {}
    meraki_by_mac: dict[str, MerakiClient] = {}
    inv_by_mac: dict[str, NetworkInventory] = {}
    snmp_by_mac = netdisco.ports_for(session, all_macs) if all_macs else {}

    if all_macs:
        for o in session.scalars(select(NetworkObservation).where(NetworkObservation.mac.in_(all_macs)).order_by(NetworkObservation.observed_at.desc())):
            obs_by_mac.setdefault(o.mac, o)
            if o.uplink_name:
                switches.setdefault(o.uplink_name.lower(), o.uplink_name)
            if o.source_name:
                sites.setdefault(o.source_name.lower(), o.source_name)
            if o.source_id:
                source_sites.setdefault((o.provider, o.source_id), o.source_name or o.source_id)
        for m in session.scalars(select(MerakiClient).where(MerakiClient.mac.in_(all_macs)).order_by(MerakiClient.observed_at.desc())):
            meraki_by_mac.setdefault(m.mac, m)
            if m.switch_name:
                switches.setdefault(m.switch_name.lower(), m.switch_name)
            if m.switch_serial:
                meraki_serials.setdefault(m.switch_serial.lower(), m.switch_name or m.switch_serial)
            if m.network_id:
                meraki_networks.setdefault(m.network_id, m.network_name or m.network_id)
            if m.network_name:
                sites.setdefault(m.network_name.lower(), m.network_name)
        for inv in session.scalars(select(NetworkInventory).where(NetworkInventory.mac.in_(all_macs)).order_by(NetworkInventory.observed_at.desc())):
            inv_by_mac.setdefault(inv.mac, inv)
            if inv.source_name:
                sites.setdefault(inv.source_name.lower(), inv.source_name)
            if inv.source_id:
                source_sites.setdefault((inv.provider, inv.source_id), inv.source_name or inv.source_id)
        for mac, port in snmp_by_mac.items():
            if port.get("switch"):
                switches.setdefault(port["switch"].lower(), port["switch"])
            if port.get("switch_id"):
                snmp_switch_ids[port["switch_id"]] = port.get("switch") or f"switch #{port['switch_id']}"

    patches = {
        p.device_id: p
        for p in session.scalars(select(DevicePatch).where(DevicePatch.device_id.in_(seen_devices or {-1}), DevicePatch.in_action1.is_(True)))
    }

    devices_out = []
    for d, role in picked:
        ips = device_ips.get(d.id, [])
        macs = [ip.mac for ip in ips if ip.mac]
        patch = patches.get(d.id)
        obs = next((obs_by_mac[m] for m in macs if m in obs_by_mac), None)
        mer = next((meraki_by_mac[m] for m in macs if m in meraki_by_mac), None)
        inv = next((inv_by_mac[m] for m in macs if m in inv_by_mac), None)
        snmp = next((snmp_by_mac[m] for m in macs if m in snmp_by_mac), None)

        online: bool | None = None
        if inv is not None and inv.state in ("online", "offline"):
            online = inv.state == "online"
        elif patch is not None and (patch.status or "").lower() in ("connected", "online", "disconnected", "offline"):
            online = (patch.status or "").lower() in ("connected", "online")
        elif mer is not None and (mer.status or "").lower() in ("online", "offline"):
            online = mer.status.lower() == "online"

        seen_times = [
            t for t in (
                patch.last_seen_at if patch else None,
                obs.last_seen_at if obs else None,
                mer.last_seen_at if mer else None,
                snmp.get("seen_at") if snmp else None,
                *(ip.last_seen_at for ip in ips),
                d.last_logon_at,
            ) if t is not None
        ]
        last_seen_at = max(seen_times) if seen_times else None
        switch_port = _where_obs(obs) if obs else (_where_meraki(mer) if mer else (_where_snmp(snmp) if snmp else ""))
        first_subnet = next((ipam.subnet_of(session, ip.address) for ip in ips if ipam.subnet_of(session, ip.address) is not None), None)
        subnet_text = _subnet_label(first_subnet) if first_subnet is not None else ""
        site_text = (
            _subnet_site(first_subnet)
            or (obs.source_name if obs and obs.source_name else "")
            or (mer.network_name if mer and mer.network_name else "")
            or (inv.source_name if inv and inv.source_name else "")
        )

        devices_out.append({
            "id": d.id,
            "name": d.name,
            "role": role,
            "online": online,
            "last_seen_at": last_seen_at,
            "switch_port": switch_port,
            "subnet": subnet_text,
            "site": site_text,
            "pending_reboot": bool(patch and patch.reboot_required),
            "critical_patches": int(patch.missing_critical if patch else 0),
            "ad_disabled": d.source == DeviceSource.directory and (not d.enabled or not d.is_active),
        })

    # Other devices sharing the same switch, subnet or site, to spot nearby problems and changes.
    nearby_device_scope: dict[int, str] = {}
    if switches or snmp_switch_ids or meraki_serials:
        switch_macs: dict[str, str] = {}
        if switches:
            for o in session.scalars(select(NetworkObservation).where(func.lower(NetworkObservation.uplink_name).in_(list(switches)))):
                if o.mac:
                    switch_macs.setdefault(o.mac, f"Switch {switches.get(o.uplink_name.lower(), o.uplink_name)}")
            for m in session.scalars(select(MerakiClient).where(func.lower(MerakiClient.switch_name).in_(list(switches)))):
                if m.mac:
                    switch_macs.setdefault(m.mac, f"Switch {switches.get(m.switch_name.lower(), m.switch_name)}")
        if meraki_serials:
            for m in session.scalars(select(MerakiClient).where(func.lower(MerakiClient.switch_serial).in_(list(meraki_serials)))):
                if m.mac:
                    switch_macs.setdefault(m.mac, f"Switch {meraki_serials.get(m.switch_serial.lower(), m.switch_serial)}")
        if snmp_switch_ids:
            for spm in session.scalars(select(SwitchPortMac).where(SwitchPortMac.network_device_id.in_(list(snmp_switch_ids)))):
                if spm.mac:
                    switch_macs.setdefault(spm.mac, f"Switch {snmp_switch_ids[spm.network_device_id]}")
            for nd_id, dev_id in session.execute(select(NetworkDevice.id, NetworkDevice.device_id).where(NetworkDevice.id.in_(list(snmp_switch_ids)), NetworkDevice.device_id.is_not(None))):
                if dev_id not in seen_devices:
                    nearby_device_scope.setdefault(dev_id, f"Switch {snmp_switch_ids[nd_id]}")
        if switches:
            for d in session.scalars(select(Device).where(func.lower(Device.name).in_(list(switches)))):
                if d.id not in seen_devices:
                    nearby_device_scope.setdefault(d.id, f"Switch {switches.get(d.name.lower(), d.name)}")
        if switch_macs:
            for mac, dev_id in session.execute(select(IpAddress.mac, IpAddress.device_id).where(IpAddress.mac.in_(list(switch_macs)), IpAddress.device_id.is_not(None))):
                if dev_id not in seen_devices:
                    nearby_device_scope.setdefault(dev_id, switch_macs[mac])

    for s in subnets.values():
        label = f"Subnet {_subnet_label(s)}"
        for dev_id in session.scalars(select(IpAddress.device_id).where(IpAddress.device_id.is_not(None), IpAddress.address.op("<<=")(func.inet(str(s.network))))):
            if dev_id not in seen_devices:
                nearby_device_scope.setdefault(dev_id, label)

    if sites:
        site_subnets = [
            s for s in session.scalars(select(Subnet).options(selectinload(Subnet.vlan)))
            if _subnet_site(s).lower() in sites
        ]
        for s in site_subnets:
            st = _subnet_site(s)
            label = f"Site {sites.get(st.lower(), st)}"
            for dev_id in session.scalars(select(IpAddress.device_id).where(IpAddress.device_id.is_not(None), IpAddress.address.op("<<=")(func.inet(str(s.network))))):
                if dev_id not in seen_devices:
                    nearby_device_scope.setdefault(dev_id, label)

    # 3. Open outages touching the device, its switch, its subnet or its site.
    outages_out = []
    parsed_subnets = []
    for s in subnets.values():
        try:
            parsed_subnets.append((s, ipam.parse_network(str(s.network))))
        except ipam.IpamError:
            pass

    for outage in session.scalars(select(NetworkOutage).where(NetworkOutage.is_down.is_(True)).order_by(NetworkOutage.down_since.desc().nulls_last())):
        inv = session.get(NetworkInventory, (outage.provider, outage.source_id, outage.device_key))
        scope = ""
        if inv is not None and inv.mac and inv.mac in mac_to_device:
            scope = f"Device {mac_to_device[inv.mac].name}"
        elif inv is not None and inv.name and inv.name.lower() in switches:
            scope = f"Switch {switches[inv.name.lower()]}"
        elif outage.device_key.lower() in switches:
            scope = f"Switch {switches[outage.device_key.lower()]}"
        elif inv is not None and inv.address:
            try:
                addr = ipam.parse_address(inv.address)
                hit = next((s for s, net in parsed_subnets if addr in net), None)
                if hit is not None:
                    scope = f"Subnet {_subnet_label(hit)}"
            except ipam.IpamError:
                pass
        if not scope:
            if (outage.provider, outage.source_id) in source_sites:
                scope = f"Site {source_sites[(outage.provider, outage.source_id)]}"
            elif inv is not None and inv.source_name and inv.source_name.lower() in sites:
                scope = f"Site {sites[inv.source_name.lower()]}"
        if not scope:
            continue
        tid, tlabel = _visible_ticket_label(session, outage.ticket_id, user)
        outages_out.append({
            "provider_label": network.LABELS.get(outage.provider, outage.provider),
            "name": (inv.name if inv and inv.name else "") or (inv.mac if inv and inv.mac else outage.device_key),
            "kind": inv.kind if inv else "other",
            "down_since": outage.down_since,
            "scope": scope,
            "ticket_id": tid if tid != ticket.id else None,
            "ticket_label": tlabel if tid != ticket.id else None,
        })

    if meraki_serials or meraki_networks:
        for inc in session.scalars(select(MerakiWebhookIncident).where(MerakiWebhookIncident.is_down.is_(True)).order_by(MerakiWebhookIncident.occurred_at.desc())):
            scope = ""
            if inc.serial.lower() in meraki_serials:
                scope = f"Switch {meraki_serials[inc.serial.lower()]}"
            elif inc.network_id in meraki_networks:
                scope = f"Site {meraki_networks[inc.network_id]}"
            if not scope:
                continue
            tid, tlabel = _visible_ticket_label(session, inc.ticket_id, user)
            outages_out.append({
                "provider_label": "Meraki",
                "name": meraki_serials.get(inc.serial.lower(), inc.serial),
                "kind": "switch",
                "down_since": inc.occurred_at,
                "scope": scope,
                "ticket_id": tid if tid != ticket.id else None,
                "ticket_label": tlabel if tid != ticket.id else None,
            })

    # 4. Open problems touching this ticket, its device, system, switch, subnet or site.
    problems_out = []
    hidden_problems = 0
    open_problems = session.scalars(
        select(Problem).where(Problem.status.in_(OPEN_PROBLEMS))
        .options(selectinload(Problem.tickets))
        .order_by(Problem.updated_at.desc(), Problem.id.desc())
    ).all()
    if open_problems:
        all_pt_ids = {t.id for p in open_problems for t in p.tickets}
        td_map: dict[int, list[int]] = {}
        ts_map: dict[int, list[int]] = {}
        if all_pt_ids:
            for tid, did in session.execute(select(TicketDevice.ticket_id, TicketDevice.device_id).where(TicketDevice.ticket_id.in_(all_pt_ids))):
                td_map.setdefault(tid, []).append(did)
            for tid, sid in session.execute(select(TicketSystem.ticket_id, TicketSystem.system_id).where(TicketSystem.ticket_id.in_(all_pt_ids))):
                ts_map.setdefault(tid, []).append(sid)

        for p in open_problems:
            pt_ids = {t.id for t in p.tickets}
            linked = ticket.id in pt_ids
            scope = "Linked to this ticket" if linked else ""
            if not scope:
                for t in p.tickets:
                    t_devs = ([t.computer_id] if t.computer_id else []) + td_map.get(t.id, [])
                    hit_dev = next((did for did in t_devs if did in device_names), None)
                    if hit_dev is not None:
                        scope = f"Device {device_names[hit_dev]}"
                        break
                    hit_sys = next((sid for sid in ts_map.get(t.id, []) if sid in system_names), None)
                    if hit_sys is not None:
                        scope = f"System {system_names[hit_sys]}"
                        break
                    hit_near = next((nearby_device_scope[did] for did in t_devs if did in nearby_device_scope), None)
                    if hit_near is not None:
                        scope = hit_near
                        break
            if not scope and p.outage_scope:
                os_low = p.outage_scope.strip().lower()
                if os_low.startswith("switch ") and os_low[7:].strip() in switches:
                    scope = p.outage_scope.strip()
                elif os_low.startswith("subnet ") and any(_subnet_label(s).lower() == os_low[7:].strip() for s in subnets.values()):
                    scope = p.outage_scope.strip()
                elif os_low.startswith("site ") and os_low[5:].strip() in sites:
                    scope = p.outage_scope.strip()
            if not scope:
                continue
            if not can_see_problem(session, p, user):
                hidden_problems += 1
                continue
            problems_out.append({
                "id": p.id,
                "label": problem_label(p),
                "title": p.title,
                "status": p.status,
                "workaround": p.workaround,
                "scope": scope,
                "linked": linked,
            })

    # 5. Recent or active changes touching this ticket, its device, system, switch, subnet or site.
    changes_out = []
    since = min(now, ticket.created_at or now) - timedelta(days=RECENT_CHANGE_DAYS)
    until = now + timedelta(hours=UPCOMING_CHANGE_HOURS)
    recent_changes = session.scalars(
        select(Change).where(Change.status.in_(RELEVANT_CHANGES))
        .options(selectinload(Change.tickets), selectinload(Change.devices), selectinload(Change.systems))
        .order_by(Change.updated_at.desc(), Change.id.desc())
        .limit(200)
    ).all()
    for c in recent_changes:
        at = c.finished_at or c.started_at or c.planned_start or c.created_at
        if c.status != ChangeStatus.in_progress and at is not None and not (since <= at <= until):
            continue
        scope = ""
        if any(t.id == ticket.id for t in c.tickets):
            scope = "Linked to this ticket"
        else:
            hit_dev = next((d for d in c.devices if d.id in device_names), None)
            if hit_dev is not None:
                scope = f"Device {hit_dev.name}"
            if not scope:
                hit_sys = next((s for s in c.systems if s.id in system_names), None)
                if hit_sys is not None:
                    scope = f"System {hit_sys.name}"
            if not scope:
                hit_near = next((nearby_device_scope[d.id] for d in c.devices if d.id in nearby_device_scope), None)
                if hit_near is not None:
                    scope = hit_near
        if not scope:
            continue
        changes_out.append({
            "id": c.id,
            "label": numbering.label(c.id, c.created_at, settings.change_label_format),
            "title": c.title,
            "status": c.status,
            "at": at,
            "scope": scope,
        })

    return {
        "account": account,
        "devices": devices_out,
        "outages": outages_out[:10],
        "problems": problems_out[:10],
        "hidden_problems": hidden_problems,
        "changes": changes_out[:10],
    }
