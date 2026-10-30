# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real LdapDirectory against the dev Samba AD (docker compose --profile ad up -d ad), or the
Windows Server lab with HELPDESK_TEST_AD=lab (tests/live_ad.py).

Skipped unless HELPDESK_TEST_LDAP=1. Uses the users and groups from dev/ad/seed.sh.
"""

import os

import pytest

from app.directory.ldap import LdapDirectory
from app.directory.types import DirectoryError
from tests.live_ad import AD

pytestmark = pytest.mark.skipif(os.environ.get("HELPDESK_TEST_LDAP") != "1", reason="set HELPDESK_TEST_LDAP=1 with the dev AD running")

BASE = AD.base_dn
CFG = dict(
    bind_dn=f"CN=svc-helpdesk,CN=Users,{BASE}",
    bind_password="Svc-readonly-pass-1",
    base_dn=BASE,
    user_filter="(&(objectCategory=person)(objectClass=user)(|(mail=*)(userPrincipalName=*)))",
    ca_file=AD.ca_file,
)


@pytest.fixture(scope="module")
def ad():
    return LdapDirectory([AD.url], **CFG)


def test_users_aliases_and_disabled(ad):
    users = {u.sam: u for u in ad.users()}
    assert {"jpark", "dcole", "mosei", "ralvarez", "leaver"} <= set(users)
    park = users["jpark"]
    assert (park.email, park.display_name, park.aliases, park.department) == (f"jpark@{AD.domain}", "J. Park", [AD.alias], "IT")
    assert users["leaver"].enabled is False and park.enabled is True


@pytest.mark.parametrize("login", ["jpark", "CORP\\jpark", f"jpark@{AD.domain}", AD.alias, "JPARK"])
def test_find_user_by_any_name(ad, login):
    assert ad.find_user(login).sam == "jpark"


def test_find_user_is_escaped(ad):
    assert ad.find_user("*") is None
    assert ad.find_user("jpark)(sAMAccountName=*") is None


def test_passwords(ad):
    park = ad.find_user("jpark")
    assert ad.check_password(park, "Correct-horse-1") is True
    assert ad.check_password(park, "wrong") is False
    assert ad.check_password(park, "") is False


def test_nested_groups_both_directions(ad):
    groups = {g.name: g for g in ad.search_groups("SG-")}
    users = {u.guid: u.sam for u in ad.users()}
    admins = ad.group_member_guids(groups["SG-Helpdesk-Admins"])
    assert {users[g] for g in admins} == {"mosei"}  # through SG-IT-Leads
    osei = ad.find_user("mosei")
    names = {g.name for g in groups.values() if g.guid in ad.user_group_guids(osei)}
    assert names == {"SG-Helpdesk-Admins", "SG-IT-Leads"}
    assert ad.group_by_guid(groups["SG-Network-Team"].guid).name == "SG-Network-Team"


def test_certificate_is_verified(ad):
    # Without our test CA the self-signed chain must be rejected.
    untrusted = LdapDirectory([AD.url], **{**CFG, "ca_file": ""})
    with pytest.raises(DirectoryError):
        untrusted.describe()


def test_wrong_service_password(ad):
    bad = LdapDirectory([AD.url], **{**CFG, "bind_password": "nope"})
    with pytest.raises(DirectoryError, match="service account was refused"):
        bad.describe()


def test_failover_to_second_server(ad):
    pool = LdapDirectory(["ldaps://localhost:1", AD.url], **{**CFG})
    assert pool.describe()["base_dn"] == BASE


def test_computers(ad):
    """From dev/ad/computers.sh."""
    computers = {c.name: c for c in ad.computers()}
    assert {AD.dc_name, "WS-0142", "WS-0007"} <= set(computers)
    ws = computers["WS-0142"]
    assert (ws.os, ws.os_version, ws.dns_hostname, ws.description) == ("Windows 11 Enterprise", "10.0 (22631)", f"ws-0142.{AD.domain}", "Finance, 2nd floor")
    assert ws.managed_by.lower() == f"cn=r alvarez,cn=users,{BASE}".lower()
    assert (ws.last_logon_at and ws.last_logon_at.year) == AD.ws0142_last_logon_year
    assert ws.enabled and not computers["WS-0007"].enabled


def test_direct_groups_nesting_and_ous(ad):
    """The AD quick action's reads (dev/ad/seed.sh: jpark in SG-Helpdesk-Agents; SG-IT-Leads inside SG-Helpdesk-Admins)."""
    park = ad.find_user("jpark")
    assert [g.name for g in ad.direct_groups(park.dn)] == ["SG-Helpdesk-Agents"]
    admins = next(g for g in ad.search_groups("SG-Helpdesk-Admins"))
    assert [g.name for g in ad.group_children(admins.dn)] == ["SG-IT-Leads"]
    leads = next(g for g in ad.search_groups("SG-IT-Leads"))
    assert [g.name for g in ad.direct_groups(leads.dn)] == ["SG-Helpdesk-Admins"]
    assert (f"OU=Workstations,{BASE}", "Workstations") in ad.search_ous("work", [BASE])


def test_account_taken_and_group_lists(ad):
    """New accounts (onboarding): names already in use, and "[EMAIL] ..." distribution lists (dev/ad/seed.sh)."""
    assert ad.account_taken(sam="jpark", upn="", mail="") == [("account name", f"CN=J Park,CN=Users,{BASE}")]
    assert ad.account_taken(sam="nobody-here", upn=f"nobody-here@{AD.domain}", mail=f"nobody-here@{AD.domain}") == []
    names = [g.name for g in ad.groups_named("[EMAIL]", [f"CN=Users,{BASE}"])]
    assert names == ["[EMAIL] All Staff", "[EMAIL] IT", "[EMAIL] Network Alerts"]
