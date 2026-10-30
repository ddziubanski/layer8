# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which Active Directory the live tests (HELPDESK_TEST_LDAP=1) talk to.

Default: the dev Samba AD (docker compose --profile ad up -d ad, domain corp.local).
HELPDESK_TEST_AD=lab: the Windows Server 2025 lab (domain corp.example.com, DC01 on 10.88.0.10), built
from docs/ad-lab.md. Both are seeded with the same people, groups and computers. The lab's CA and keytab
come from HELPDESK_TEST_AD_CA and HELPDESK_TEST_AD_KEYTAB (defaults: C:\\layer8-lab seen from WSL).
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEV_AD = Path(__file__).resolve().parents[2] / "dev" / "ad" / "out"
LAB = Path("/mnt/c/layer8-lab")


@dataclass(frozen=True)
class LiveAd:
    domain: str  # DNS name; also the mail domain and the realm (upper case)
    url: str  # LDAPS; the host name must match the DC's certificate
    ca_file: str
    keytab: str
    kdc: str  # host:port
    alias: str  # jpark's extra proxyAddress
    dc_name: str
    ws0142_last_logon_year: int | None  # Samba lets the seed set lastLogonTimestamp; Windows doesn't

    @property
    def base_dn(self) -> str:
        return ",".join(f"DC={part}" for part in self.domain.split("."))

    @property
    def realm(self) -> str:
        return self.domain.upper()

    @property
    def portal_host(self) -> str:
        return f"helpdesk.{self.domain}"


SAMBA = LiveAd(
    domain="corp.local",
    url="ldaps://localhost:1636",
    ca_file=str(DEV_AD / "ca.pem"),
    keytab=str(DEV_AD / "http.keytab"),
    kdc="127.0.0.1:1088",
    alias="j.park@corp.com",
    dc_name="DC1",
    ws0142_last_logon_year=2025,
)

WINDOWS_LAB = LiveAd(
    domain="corp.example.com",
    url="ldaps://dc01.corp.example.com:636",
    ca_file=os.environ.get("HELPDESK_TEST_AD_CA", str(LAB / "corp-ca.pem")),
    keytab=os.environ.get("HELPDESK_TEST_AD_KEYTAB", str(LAB / "http.keytab")),
    kdc="10.88.0.10:88",
    alias="j.park@example.com",
    dc_name="DC01",
    ws0142_last_logon_year=None,
)

AD = WINDOWS_LAB if os.environ.get("HELPDESK_TEST_AD") == "lab" else SAMBA
