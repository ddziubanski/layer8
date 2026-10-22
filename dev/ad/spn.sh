#!/bin/sh
# The portal's Kerberos service account and SPN, and its keytab in /out for the app.
# Runs on every start (idempotent), so existing dev domains pick it up too.
set -eu
for i in $(seq 60); do samba-tool user list >/dev/null 2>&1 && break; sleep 1; done
SPN=HTTP/helpdesk.corp.local
if ! samba-tool user show svc-portal >/dev/null 2>&1; then
  samba-tool user create svc-portal 'Svc-portal-pass-1' --description="Helpdesk portal SSO (SPN $SPN)" >/dev/null
  samba-tool user setexpiry svc-portal --noexpiry >/dev/null
  # AES only (RC4 is deprecated and refused by current Kerberos); the password reset makes the AES keys.
  ldbmodify -H /var/lib/samba/private/sam.ldb >/dev/null <<LDIF
dn: CN=svc-portal,CN=Users,DC=corp,DC=local
changetype: modify
replace: msDS-SupportedEncryptionTypes
msDS-SupportedEncryptionTypes: 24
LDIF
  samba-tool user setpassword svc-portal --newpassword='Svc-portal-pass-2' >/dev/null
fi
samba-tool spn list svc-portal | grep -q "$SPN" || samba-tool spn add "$SPN" svc-portal >/dev/null
rm -f /out/http.keytab
samba-tool domain exportkeytab /out/http.keytab --principal="$SPN" >/dev/null
# Readable by the developer running the app on the host; a real keytab is root/service-only.
chmod 644 /out/http.keytab
echo "spn: $SPN keytab exported to /out/http.keytab"
