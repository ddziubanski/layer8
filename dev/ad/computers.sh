#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Test computers (become devices through directory sync). Runs once, separately from seed.sh
# so domains seeded before computers existed get them too.
set -eu
MARK=/var/lib/samba/.seeded-computers
[ -f "$MARK" ] && exit 0
for i in $(seq 90); do [ -f /var/lib/samba/.seeded ] && break; sleep 1; done
BASE='DC=corp,DC=local'
samba-tool ou create "OU=Workstations,$BASE" >/dev/null
samba-tool computer create WS-0142 --computerou="OU=Workstations" --description="Finance, 2nd floor" >/dev/null
samba-tool computer create WS-0007 --computerou="OU=Workstations" >/dev/null
ldbmodify -H /var/lib/samba/private/sam.ldb >/dev/null <<LDIF
dn: CN=WS-0142,OU=Workstations,$BASE
changetype: modify
replace: operatingSystem
operatingSystem: Windows 11 Enterprise
-
replace: operatingSystemVersion
operatingSystemVersion: 10.0 (22631)
-
replace: dNSHostName
dNSHostName: ws-0142.corp.local
-
replace: managedBy
managedBy: CN=R Alvarez,CN=Users,$BASE
-
replace: lastLogonTimestamp
lastLogonTimestamp: 134050000000000000
-
replace: userAccountControl
userAccountControl: 4096
LDIF
# samba-tool creates computer accounts disabled (4098); WS-0007 stays that way, like a retired PC.
touch "$MARK"
echo "seed: CORP.LOCAL test computers ready"
