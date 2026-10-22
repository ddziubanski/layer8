#!/bin/sh
# Provision CORP.LOCAL on first start, with our own test CA so LDAPS can be verified.
set -eu
TLS=/var/lib/samba/tls-dev
if [ ! -f /var/lib/samba/private/sam.ldb ]; then
  mkdir -p "$TLS"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=Helpdesk Dev Test CA" \
    -keyout "$TLS/ca.key" -out "$TLS/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=dc1.corp.local" -keyout "$TLS/key.pem" -out "$TLS/req.csr" 2>/dev/null
  printf 'subjectAltName=DNS:dc1.corp.local,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > "$TLS/ext.cnf"
  openssl x509 -req -in "$TLS/req.csr" -CA "$TLS/ca.pem" -CAkey "$TLS/ca.key" -CAcreateserial -days 3650 \
    -extfile "$TLS/ext.cnf" -out "$TLS/cert.pem" 2>/dev/null
  chmod 600 "$TLS/key.pem"
  rm -f /etc/samba/smb.conf
  samba-tool domain provision --use-rfc2307 --realm=CORP.LOCAL --domain=CORP --server-role=dc \
    --dns-backend=SAMBA_INTERNAL --host-name=dc1 --adminpass='Dev-admin-pass-1' \
    --option="tls enabled = yes" --option="tls keyfile = $TLS/key.pem" \
    --option="tls certfile = $TLS/cert.pem" --option="tls cafile = $TLS/ca.pem"
  cp /etc/samba/smb.conf /var/lib/samba/smb.conf.dev
fi
# Provisioning writes smb.conf into the image, which a rebuild loses: keep it in the volume.
if [ ! -f /var/lib/samba/smb.conf.dev ]; then
  # Domain provisioned by an older image: recreate its config.
  cat > /var/lib/samba/smb.conf.dev <<CONF
[global]
	netbios name = DC1
	realm = CORP.LOCAL
	server role = active directory domain controller
	workgroup = CORP
	idmap_ldb:use rfc2307 = yes
	tls enabled = yes
	tls keyfile = $TLS/key.pem
	tls certfile = $TLS/cert.pem
	tls cafile = $TLS/ca.pem

[sysvol]
	path = /var/lib/samba/sysvol
	read only = No

[netlogon]
	path = /var/lib/samba/sysvol/corp.local/scripts
	read only = No
CONF
fi
cp /var/lib/samba/smb.conf.dev /etc/samba/smb.conf
# Hand the CA to the host so the app can verify the DC's certificate.
mkdir -p /out && cp "$TLS/ca.pem" /out/ca.pem
/usr/local/bin/seed.sh &
/usr/local/bin/spn.sh &
/usr/local/bin/computers.sh &
exec samba --foreground --no-process-group
