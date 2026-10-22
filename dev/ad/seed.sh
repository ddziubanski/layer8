#!/bin/sh
# Test people and groups, matching tests/fake_directory.py scenarios. Runs once.
set -eu
MARK=/var/lib/samba/.seeded
[ -f "$MARK" ] && exit 0
for i in $(seq 60); do samba-tool user list >/dev/null 2>&1 && break; sleep 1; done
PW='Correct-horse-1'
BASE='DC=corp,DC=local'
user() { samba-tool user create "$1" "$PW" --given-name="$2" --surname="$3" --mail-address="$1@corp.local" >/dev/null; }

samba-tool user create svc-helpdesk 'Svc-readonly-pass-1' --description="Helpdesk read-only bind" >/dev/null
user jpark J Park
user dcole D Cole
user mosei M Osei
user ralvarez R Alvarez
user leaver L Eaver
user newhire New Hire
samba-tool user disable leaver >/dev/null

samba-tool group add SG-Helpdesk-Admins >/dev/null
samba-tool group add SG-Helpdesk-Agents >/dev/null
samba-tool group add SG-IT-Leads >/dev/null
samba-tool group add SG-Network-Team >/dev/null
samba-tool group addmembers SG-IT-Leads mosei >/dev/null
# Nested: IT leads are helpdesk admins through the group.
samba-tool group addmembers SG-Helpdesk-Admins SG-IT-Leads >/dev/null
samba-tool group addmembers SG-Helpdesk-Agents jpark,dcole >/dev/null
samba-tool group addmembers SG-Network-Team dcole >/dev/null
# Distribution lists, named the way the site does: "[EMAIL] ..." in the name (sAMAccountName can't hold "[").
for dl in "All Staff" "IT" "Network Alerts"; do
  sam=$(echo "EMAIL-$dl" | tr ' ' '-')
  ldbadd -H /var/lib/samba/private/sam.ldb >/dev/null <<LDIF
dn: CN=[EMAIL] $dl,CN=Users,$BASE
objectClass: group
sAMAccountName: $sam
displayName: [EMAIL] $dl
groupType: 8
LDIF
done
samba-tool group add "M365-E3" >/dev/null

# Aliases (proxyAddresses), display names and departments, as Exchange/AD would have them.
ldbmodify -H /var/lib/samba/private/sam.ldb >/dev/null <<LDIF
dn: CN=J Park,CN=Users,$BASE
changetype: modify
replace: proxyAddresses
proxyAddresses: SMTP:jpark@corp.local
proxyAddresses: smtp:j.park@corp.com
-
replace: displayName
displayName: J. Park
-
replace: department
department: IT

dn: CN=R Alvarez,CN=Users,$BASE
changetype: modify
replace: proxyAddresses
proxyAddresses: SMTP:ralvarez@corp.local
proxyAddresses: smtp:frontdesk@corp.local
-
replace: displayName
displayName: R. Alvarez

dn: CN=D Cole,CN=Users,$BASE
changetype: modify
replace: displayName
displayName: D. Cole

dn: CN=M Osei,CN=Users,$BASE
changetype: modify
replace: displayName
displayName: M. Osei
LDIF
touch "$MARK"
echo "seed: CORP.LOCAL test users and groups ready"
