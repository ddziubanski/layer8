# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket context (#157): before anyone starts guessing, show requester account state, device live
state, and open outages, problems and recent changes touching the device, switch, subnet or site."""
from datetime import timedelta

from app import services, sla
from app.directory import ldap, sync as dsync
from app.models import (
    AuthSource,
    Change,
    ChangeStatus,
    Device,
    DevicePatch,
    DeviceSource,
    DirectorySyncRun,
    IpAddress,
    NetworkInventory,
    NetworkObservation,
    NetworkOutage,
    Problem,
    ProblemStatus,
    Queue,
    Subnet,
    TicketDevice,
    TicketSource,
    User,
    UserRole,
)
from tests.conftest import as_user

MAC_PC = "aa:bb:cc:11:22:33"
MAC_PEER = "aa:bb:cc:11:22:44"


def test_ldap_syncs_ad_lockout_and_account_expiry(db):
    now = sla.utcnow()
    expires = now + timedelta(days=5)
    # 100-nanosecond intervals since 1601-01-01.
    ft_lock = int((now - ldap.FILETIME_EPOCH).total_seconds() * 10_000_000)
    ft_exp = int((expires - ldap.FILETIME_EPOCH).total_seconds() * 10_000_000)
    d = ldap.LdapDirectory.__new__(ldap.LdapDirectory)
    d._kind = "ad"
    entry = {
        "dn": "CN=R. Alvarez,OU=Staff,DC=corp,DC=example",
        "raw_attributes": {
            "objectGUID": [b"\x01" * 16],
            "lockoutTime": [str(ft_lock).encode()],
            "accountExpires": [str(ft_exp).encode()],
        },
        "attributes": {
            "mail": "ralvarez@corp.example",
            "displayName": "R. Alvarez",
            "userAccountControl": "512",
            "sAMAccountName": "ralvarez",
        },
    }
    du = d._user(entry)
    assert du.locked is True
    assert du.account_expires_at is not None and abs((du.account_expires_at - expires).total_seconds()) < 2

    run = DirectorySyncRun(started_at=now)
    db.add(run)
    db.flush()
    user = dsync.upsert_user(db, du, now, run)
    db.flush()
    assert user.ad_locked is True
    assert user.account_expires_at == du.account_expires_at


def test_requester_account_and_assigned_device_live_state(client, db, agent, requester, service_desk):
    now = sla.utcnow()
    requester.auth_source = AuthSource.directory
    requester.ad_locked = True
    requester.is_active = False
    requester.account_expires_at = now + timedelta(days=4)

    # Assigned to the requester, though the ticket didn't pick a computer yet.
    pc = Device(name="WS-0142", source=DeviceSource.directory, assigned_user_id=requester.id, enabled=True, is_active=True)
    db.add(pc)
    db.flush()
    db.add(IpAddress(address="10.20.0.42", mac=MAC_PC, device_id=pc.id, last_seen_at=now - timedelta(minutes=15)))
    db.add(Subnet(network="10.20.0.0/24", name="Staff Wi-Fi", site="HQ"))
    db.add(DevicePatch(
        action1_id="a1-42", organization_id="org", name="WS-0142", device_id=pc.id,
        status="Connected", last_seen_at=now - timedelta(minutes=3), reboot_required=True, missing_critical=2,
    ))
    db.add(NetworkObservation(
        provider="unifi", source_id="site-hq", source_name="HQ", mac=MAC_PC,
        uplink_name="SW-2F", port="12", network="Staff", last_seen_at=now - timedelta(minutes=1), observed_at=now,
    ))
    ticket = services.create_ticket(
        db, subject="Can't sign in", description="Locked out again", category=None,
        requester=requester, source=TicketSource.portal, actor=requester,
    )
    ticket.computer_id = None
    db.commit()

    res = client.get(f"/api/tickets/{ticket.id}/context", headers=as_user(agent))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [a["kind"] for a in body["account"]] == ["disabled", "locked", "expiring"]
    assert len(body["devices"]) == 1
    dev = body["devices"][0]
    assert dev["name"] == "WS-0142" and dev["role"] == "requester"
    assert dev["online"] is True
    assert dev["switch_port"] == "SW-2F port 12 · Staff"
    assert dev["subnet"] == "10.20.0.0/24 (Staff Wi-Fi)"
    assert dev["site"] == "HQ"
    assert dev["pending_reboot"] is True and dev["critical_patches"] == 2


def test_nearby_outages_problems_and_changes_across_switch_subnet_and_site(client, db, agent, requester, service_desk):
    now = sla.utcnow()
    pc = Device(name="WS-0142", assigned_user_id=requester.id)
    peer = Device(name="WS-0199")
    db.add_all([pc, peer])
    db.flush()
    db.add(Subnet(network="10.20.0.0/24", name="Floor 2", site="HQ"))
    db.add(IpAddress(address="10.20.0.42", mac=MAC_PC, device_id=pc.id))
    db.add(IpAddress(address="10.20.0.99", mac=MAC_PEER, device_id=peer.id))
    db.add(NetworkObservation(
        provider="unifi", source_id="site-hq", source_name="HQ", mac=MAC_PC,
        uplink_name="SW-2F", port="7", network="Floor 2", observed_at=now,
    ))

    # Outage on the switch WS-0142 plugs into.
    outage_ticket = services.create_ticket(
        db, subject="Offline: UniFi switch SW-2F", description="Down", category=None,
        requester=agent, source=TicketSource.api, actor=agent,
    )
    db.add(NetworkInventory(
        provider="unifi", source_id="site-hq", device_key="sw-2f", source_name="HQ",
        name="SW-2F", kind="switch", state="offline", observed_at=now,
    ))
    db.add(NetworkOutage(
        provider="unifi", source_id="site-hq", device_key="sw-2f",
        is_down=True, down_since=now - timedelta(minutes=25), ticket_id=outage_ticket.id, changed_at=now,
    ))

    # Problem linked to another ticket on a peer device in the same subnet, with a workaround.
    peer_ticket = services.create_ticket(
        db, subject="DHCP renewals failing on floor 2", description="No lease", category=None,
        requester=requester, source=TicketSource.portal, actor=requester, computer=peer,
    )
    prob = Problem(
        title="Floor 2 DHCP helper", status=ProblemStatus.known_error,
        workaround="Reconnect to Guest Wi-Fi while the relay is fixed.", tickets=[peer_ticket],
    )
    db.add(prob)

    # Another problem that links a restricted ticket `agent` cannot see: counted, never named.
    secret_queue = Queue(name="HR Confidential", restricted=True)
    db.add(secret_queue)
    db.flush()
    secret_ticket = services.create_ticket(
        db, subject="Private", description="Private", category=None,
        requester=requester, source=TicketSource.agent, actor=requester, requested_queue=secret_queue, computer=pc,
    )
    db.add(Problem(title="Secret problem", status=ProblemStatus.open, tickets=[secret_ticket]))

    # A recent change touching the peer device in the same subnet, and an old change (> 7 days) that shouldn't show.
    recent_chg = Change(
        title="Upgrade Floor 2 distribution config", status=ChangeStatus.completed,
        finished_at=now - timedelta(hours=3), devices=[peer],
    )
    old_chg = Change(
        title="Last month's maintenance", status=ChangeStatus.completed,
        finished_at=now - timedelta(days=20), devices=[pc],
    )
    db.add_all([recent_chg, old_chg])

    ticket = services.create_ticket(
        db, subject="No internet at my desk", description="Browser says offline", category=None,
        requester=requester, source=TicketSource.portal, actor=requester, computer=pc,
    )
    db.commit()

    body = client.get(f"/api/tickets/{ticket.id}/context", headers=as_user(agent)).json()
    assert len(body["outages"]) == 1
    assert body["outages"][0]["name"] == "SW-2F"
    assert body["outages"][0]["scope"] == "Switch SW-2F"
    assert body["outages"][0]["ticket_id"] == outage_ticket.id

    assert len(body["problems"]) == 1
    assert body["problems"][0]["title"] == "Floor 2 DHCP helper"
    assert body["problems"][0]["workaround"] == "Reconnect to Guest Wi-Fi while the relay is fixed."
    assert body["problems"][0]["scope"] == "Subnet 10.20.0.0/24 (Floor 2)"
    assert body["hidden_problems"] == 1

    assert [c["title"] for c in body["changes"]] == ["Upgrade Floor 2 distribution config"]
    assert body["changes"][0]["scope"] == "Subnet 10.20.0.0/24 (Floor 2)"

    # Requesters cannot read staff ticket context.
    assert client.get(f"/api/tickets/{ticket.id}/context", headers=as_user(requester)).status_code == 403
    assert client.get(f"/api/tickets/{secret_ticket.id}/context", headers=as_user(agent)).status_code == 404
