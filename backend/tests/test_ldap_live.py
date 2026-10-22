"""The real LdapDirectory against the dev Samba AD (docker compose --profile ad up -d ad).

Skipped unless HELPDESK_TEST_LDAP=1. Uses the users and groups from dev/ad/seed.sh.
"""

import os
from pathlib import Path

import pytest

from app.directory.ldap import LdapDirectory
from app.directory.types import DirectoryError

pytestmark = pytest.mark.skipif(os.environ.get("HELPDESK_TEST_LDAP") != "1", reason="set HELPDESK_TEST_LDAP=1 with the dev AD running")

CA = str(Path(__file__).resolve().parents[2] / "dev" / "ad" / "out" / "ca.pem")
CFG = dict(
    bind_dn="CN=svc-helpdesk,CN=Users,DC=corp,DC=local",
    bind_password="Svc-readonly-pass-1",
    base_dn="DC=corp,DC=local",
    user_filter="(&(objectCategory=person)(objectClass=user)(|(mail=*)(userPrincipalName=*)))",
    ca_file=CA,
)


@pytest.fixture(scope="module")
def ad():
    return LdapDirectory(["ldaps://localhost:1636"], **CFG)


def test_users_aliases_and_disabled(ad):
    users = {u.sam: u for u in ad.users()}
    assert {"jpark", "dcole", "mosei", "ralvarez", "leaver"} <= set(users)
    park = users["jpark"]
    assert (park.email, park.display_name, park.aliases, park.department) == ("jpark@corp.local", "J. Park", ["j.park@corp.com"], "IT")
    assert users["leaver"].enabled is False and park.enabled is True


@pytest.mark.parametrize("login", ["jpark", "CORP\\jpark", "jpark@corp.local", "j.park@corp.com", "JPARK"])
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
    untrusted = LdapDirectory(["ldaps://localhost:1636"], **{**CFG, "ca_file": ""})
    with pytest.raises(DirectoryError):
        untrusted.describe()


def test_wrong_service_password(ad):
    bad = LdapDirectory(["ldaps://localhost:1636"], **{**CFG, "bind_password": "nope"})
    with pytest.raises(DirectoryError, match="service account was refused"):
        bad.describe()


def test_failover_to_second_server(ad):
    pool = LdapDirectory(["ldaps://localhost:1", "ldaps://localhost:1636"], **{**CFG})
    assert pool.describe()["base_dn"] == "DC=corp,DC=local"


def test_computers(ad):
    """From dev/ad/computers.sh."""
    computers = {c.name: c for c in ad.computers()}
    assert {"DC1", "WS-0142", "WS-0007"} <= set(computers)
    ws = computers["WS-0142"]
    assert (ws.os, ws.os_version, ws.dns_hostname, ws.description) == ("Windows 11 Enterprise", "10.0 (22631)", "ws-0142.corp.local", "Finance, 2nd floor")
    assert ws.managed_by.lower() == "cn=r alvarez,cn=users,dc=corp,dc=local"
    assert ws.last_logon_at is not None and ws.last_logon_at.year == 2025
    assert ws.enabled and not computers["WS-0007"].enabled


def test_direct_groups_nesting_and_ous(ad):
    """The AD quick action's reads (dev/ad/seed.sh: jpark in SG-Helpdesk-Agents; SG-IT-Leads inside SG-Helpdesk-Admins)."""
    park = ad.find_user("jpark")
    assert [g.name for g in ad.direct_groups(park.dn)] == ["SG-Helpdesk-Agents"]
    admins = next(g for g in ad.search_groups("SG-Helpdesk-Admins"))
    assert [g.name for g in ad.group_children(admins.dn)] == ["SG-IT-Leads"]
    leads = next(g for g in ad.search_groups("SG-IT-Leads"))
    assert [g.name for g in ad.direct_groups(leads.dn)] == ["SG-Helpdesk-Admins"]
    assert ("OU=Workstations,DC=corp,DC=local", "Workstations") in ad.search_ous("work", ["DC=corp,DC=local"])


def test_account_taken_and_group_lists(ad):
    """New accounts (onboarding): names already in use, and "[EMAIL] ..." distribution lists (dev/ad/seed.sh)."""
    assert ad.account_taken(sam="jpark", upn="", mail="") == [("account name", "CN=J Park,CN=Users,DC=corp,DC=local")]
    assert ad.account_taken(sam="nobody-here", upn="nobody-here@corp.local", mail="nobody-here@corp.local") == []
    names = [g.name for g in ad.groups_named("[EMAIL]", ["CN=Users,DC=corp,DC=local"])]
    assert names == ["[EMAIL] All Staff", "[EMAIL] IT", "[EMAIL] Network Alerts"]
