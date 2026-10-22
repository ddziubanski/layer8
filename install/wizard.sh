#!/bin/sh
# Layer 8 setup wizard: asks for every option once, writes the answers file and runs the
# installer. The appliance ISO starts it on first boot; on any Debian 13 server run it as root:
#
#   sudo ./install/wizard.sh
#
# Everything it asks can be changed later in the app (Settings), except the host name and the
# HTTPS certificate, which are server settings (re-run the wizard, or see docs/install.md).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ANSWERS=/root/helpdesk-answers.json
TITLE="Layer 8 setup"
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
command -v whiptail >/dev/null || apt-get install -y -q whiptail >/dev/null

# --- Small helpers around whiptail (answers come back on stderr)

ask() { # ask "Question" "default" -> stdout
  whiptail --title "$TITLE" --inputbox "$1" 12 76 "${2:-}" 3>&1 1>&2 2>&3
}
secret() { # secret "Question" -> stdout (asked twice when $2 = twice)
  while :; do
    a=$(whiptail --title "$TITLE" --passwordbox "$1" 10 76 3>&1 1>&2 2>&3)
    [ "${2:-}" = twice ] || { printf '%s' "$a"; return; }
    b=$(whiptail --title "$TITLE" --passwordbox "Once more, to check:" 10 76 3>&1 1>&2 2>&3)
    [ "$a" = "$b" ] && { printf '%s' "$a"; return; }
    whiptail --title "$TITLE" --msgbox "They didn't match. Try again." 8 50
  done
}
yesno() { whiptail --title "$TITLE" --yesno "$1" 12 76; }
note() { whiptail --title "$TITLE" --msgbox "$1" 14 76; }

# Answers are kept as KEY=VALUE lines, then turned into JSON by Python (proper escaping).
VALUES=$(mktemp)
trap 'rm -f "$VALUES"' EXIT
chmod 600 "$VALUES"
put() { printf '%s\t%s\n' "$1" "$(printf '%s' "$2" | base64 -w0)" >> "$VALUES"; }

# --- 1. What kind of install

MODE=$(whiptail --title "$TITLE" --menu "Welcome to Layer 8, the service desk for the layer every network has: its people.\n\nWhat would you like to set up?" 15 76 3 \
  install "Layer 8, for real" \
  demo "Offline demo: sample data, nothing leaves this machine" \
  file "From an answers file I already have" 3>&1 1>&2 2>&3) || exit 1

if [ "$MODE" = demo ]; then
  yesno "Install the offline demo?\n\nSample people, tickets, devices and scripts, with local accounts only and no email or AD. It's clearly marked as a demo. You can reinstall for real later." || exit 1
  exec "$HERE/install.sh" --demo
fi
if [ "$MODE" = file ]; then
  path=$(ask "Path to the answers file (see docs/install.md):" "$ANSWERS")
  exec "$HERE/install.sh" --answers "$path"
fi

# --- 1b. The OS administrator (set now, so files such as a certificate can be copied in over SSH)

note "The OS administrator (sdadmin): a full shell on this machine, on its screen or over SSH. It has no password until you choose one here, so nobody can log in before you."
while :; do
  opw=$(secret "Password for the OS administrator (sdadmin), 12 characters or more:" twice)
  [ "${#opw}" -ge 12 ] && break
  note "That's ${#opw} characters; use at least 12."
done
printf 'sdadmin:%s\n' "$opw" | chpasswd
put install.os_password "$opw"

# --- 2. Address and HTTPS

HOST=$(ask "The service desk's DNS name. People open https://<this name>, and Windows sign-in needs it.\n\nExample: helpdesk.corp.example" "$(hostname -f 2>/dev/null || hostname)")
put install.hostname "$HOST"
put general.portal_url "https://$HOST"
if yesno "HTTPS certificate:\n\nYes: I have a certificate and key file (PEM) for $HOST from our CA.\nNo: make a self-signed one for now (browsers will warn until it's replaced)."; then
  note "Copy the files onto this machine now if they aren't here yet, e.g. from your PC:\n\n  scp cert.pem key.pem sdadmin@<this machine's address>:\n\n(this machine's address: $(hostname -I 2>/dev/null | cut -d' ' -f1))"
  put install.tls.cert "$(ask "Path to the certificate file (PEM, with any intermediate certificates):" "/home/sdadmin/cert.pem")"
  put install.tls.key "$(ask "Path to its private key file (PEM):" "/home/sdadmin/key.pem")"
else
  put install.tls self-signed
fi

tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
while :; do
  tz=$(ask "Time zone (backup times, scheduled reports and business hours follow it).\n\nExamples: America/Chicago   America/New_York   Europe/London   UTC" "$tz")
  [ -f "/usr/share/zoneinfo/$tz" ] && break
  note "\"$tz\" isn't a time zone name; use one like America/Chicago."
done
put install.timezone "$tz"

# --- 3. First admin

note "Next: the first admin, a local account that always works, even when AD is down (keep it as a break-glass account)."
put install.admin.email "$(ask "Admin's email or username:" "admin@$(echo "$HOST" | cut -d. -f2-)")"
put install.admin.name "$(ask "Admin's name:" "Service Desk Admin")"
while :; do
  pw=$(secret "Admin's password (12 characters or more):" twice)
  [ "${#pw}" -ge 12 ] && break
  note "That's ${#pw} characters; use at least 12."
done
put install.admin.password "$pw"

note "The recovery console: log in as \"layer8\" on this machine's screen or over SSH for a menu to fix the network, DNS, break-glass admin passwords, services and backups, without a full shell (that still needs the sdadmin password)."
while :; do
  rpw=$(secret "Password for the recovery console (layer8), 12 characters or more:" twice)
  [ "${#rpw}" -ge 12 ] && break
  note "That's ${#rpw} characters; use at least 12."
done
put install.recovery.password "$rpw"


# --- 4. Ticket numbers

put general.ticket_first_number "$(ask "First ticket number:" "1000")"
put general.ticket_label_format "$(ask "How ticket numbers are shown and put in email subjects.\n{n} is the number, {n:06} pads it to six digits, {yyyy} is the year.\n\nExamples: #{n}   HD-{n}   INC{n:06}   {yyyy}-{n}" "#{n}")"

# --- 5. Microsoft Entra (email in and out)

if yesno "Microsoft Entra / Exchange Online: should tickets come in by email and notifications go out?\n\nYou need an app registration in Entra (tenant ID, client ID, client secret) with Mail.ReadWrite and Mail.Send limited to the service desk mailbox (docs/email-setup.md).\n\nYou can also do this later in Settings > Email."; then
  put email.graph_tenant_id "$(ask "Directory (tenant) ID, from the app registration in the Entra admin center:")"
  put email.graph_client_id "$(ask "Application (client) ID:")"
  put email.graph_client_secret "$(secret "Client secret (its value, not its ID):")"
  put email.mailbox "$(ask "Service desk mailbox, e.g. helpdesk@example.com:")"
  put email.mail_allowed_domains "$(ask "Accept email from these domains (comma-separated), e.g. example.com:")"
  CHECK_EMAIL=1
fi

# --- 6. Active Directory

if yesno "Active Directory: sync people, groups and computers, and let staff sign in with their AD password?\n\nYou need LDAPS to a domain controller and a read-only service account.\n\nYou can also do this later in Settings > Directory."; then
  put directory.ldap_url "$(ask "Domain controllers (comma-separated), e.g. ldaps://dc1.corp.example,ldaps://dc2.corp.example:")"
  put directory.ldap_bind_dn "$(ask "Service account (DN), e.g. CN=svc-helpdesk,OU=Service Accounts,DC=corp,DC=example:")"
  put directory.ldap_bind_password "$(secret "Service account password:")"
  put directory.ldap_base_dn "$(ask "Search base, e.g. DC=corp,DC=example:")"
  ca=$(ask "CA certificate for the domain controllers' LDAPS (PEM file path). Empty: the system's trusted CAs." "")
  [ -n "$ca" ] && put directory.ldap_ca_file "@file:$ca"
  if yesno "Are your distribution lists in their own OU?\n\nIf so, every group in that OU is offered as tick boxes when technicians add someone to groups (on a ticket, a new account, a template), and technicians may add and remove people in those groups.\n\nIf not, Settings > Directory > Group lists can match them by name instead (e.g. names starting with [EMAIL])."; then
    put directory.ad_group_lists "Distribution lists: $(ask "That OU's DN, e.g. OU=Distribution Lists,DC=corp,DC=example:")"
  fi
  CHECK_AD=1
fi

# --- 6b. Action1 (patching)

if yesno "Do you use Action1 for patching?\n\nIf so, Layer 8 reads it (read-only): each device's patch status on devices and tickets, tickets for overdue patching, and patch nights on the change calendar. You need API credentials from Action1 > Configuration > API credentials (docs/action1.md).\n\nIf not, everything Action1 stays hidden. You can switch it on later in Settings > General."; then
  put general.action1_enabled "true"
  region=$(whiptail --title "$TITLE" --menu "Where is your Action1 console?" 14 70 5 \
    NorthAmerica "North America (app.action1.com)" NorthAmerica-2 "North America 2 (app.na-2.action1.com)" \
    Europe "Europe (app.eu.action1.com)" UnitedKingdom "United Kingdom (app.uk.action1.com)" Australia "Australia (app.au.action1.com)" 3>&1 1>&2 2>&3) || region=NorthAmerica
  put action1.action1_region "$region"
  put action1.action1_client_id "$(ask "Action1 API client ID:")"
  put action1.action1_client_secret "$(secret "Action1 API client secret:")"
fi

# --- 7. Windows sign-in for the portal

if yesno "Windows sign-in for the portal (people on domain PCs are signed in as themselves)?\n\nYou need the keytab for HTTP/$HOST (docs/windows-sign-in.md). Also possible later in Settings > Windows sign-in."; then
  put windows.keytab_file "$(ask "Path to the keytab file:" "/root/http.keytab")"
fi

# --- 8. Backups

if yesno "Nightly encrypted backups to a network share?\n\nThe share is mounted on this server at /mnt/helpdesk-backup. Also possible later in Settings > Backup (with the share mounted by hand)."; then
  kind=$(whiptail --title "$TITLE" --menu "Share type:" 12 60 2 smb "Windows / SMB share" nfs "NFS export" 3>&1 1>&2 2>&3)
  put install.backup.kind "$kind"
  if [ "$kind" = smb ]; then
    put install.backup.share "$(ask "Share, e.g. //fileserver/backups/helpdesk:")"
    put install.backup.user "$(ask "Account for the share (DOMAIN\\\\user or user):")"
    put install.backup.password "$(secret "Its password:")"
  else
    put install.backup.share "$(ask "NFS export, e.g. nas.corp.example:/export/helpdesk:")"
  fi
  put backup.backup_dir /mnt/helpdesk-backup
  put backup.backup_time "$(ask "Back up daily at (24-hour):" "02:00")"
  while :; do
    bp=$(secret "Backup passphrase (12+ characters). Keep a copy somewhere safe away from this server: without it a backup can't be restored." twice)
    [ "${#bp}" -ge 12 ] && break
    note "Use at least 12 characters."
  done
  put backup.backup_passphrase "$bp"
fi

# --- Summary, then install

python3 - "$VALUES" "$ANSWERS" <<'PY'
import base64, json, os, sys
values, out = sys.argv[1], sys.argv[2]
answers = {}
for line in open(values):
    key, raw = line.rstrip("\n").split("\t", 1)
    value = base64.b64decode(raw).decode()
    if value.startswith("@file:"):
        value = {"file": value[6:]}
    elif key in ("general.ticket_first_number",):
        value = int(value)
    elif key == "email.mail_allowed_domains":
        value = [d.strip() for d in value.split(",") if d.strip()]
    elif key == "general.action1_enabled":
        value = value == "true"
    elif key == "directory.ad_group_lists":
        # One "Label: OU=...,DC=..." line: the DN's commas aren't separators.
        value = [value]
    if key.startswith("windows."):
        answers.setdefault("windows_sign_in", {}).update({"keytab_file": value, "enabled": True, "silent": False})
        continue
    node, parts = answers, key.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(answers, f, indent=1)
PY

SUMMARY="Ready to install.\n\nAddress: https://$HOST\nAdmin: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["install"]["admin"]["email"])' "$ANSWERS")"
grep -q '"email"' "$ANSWERS" && SUMMARY="$SUMMARY\nEmail: Microsoft Entra / Exchange Online"
grep -q '"directory"' "$ANSWERS" && SUMMARY="$SUMMARY\nDirectory: Active Directory"
grep -q '"backup"' "$ANSWERS" && SUMMARY="$SUMMARY\nBackups: nightly to a network share"
grep -q '"action1_enabled": true' "$ANSWERS" && SUMMARY="$SUMMARY\nPatching: Action1"
yesno "$SUMMARY\n\nThe answers (including passwords) are in $ANSWERS, readable by root only, until the install finishes.\n\nInstall now?" || { echo "Not installed. Answers kept in $ANSWERS."; exit 1; }

"$HERE/install.sh" --answers "$ANSWERS"

# --- Checks with what was saved

checks=""
if [ -n "${CHECK_EMAIL:-}" ]; then
  checks="$checks\n$(su helpdesk -s /bin/sh -c 'set -a; . /etc/helpdesk/helpdesk.env; set +a; cd /opt/helpdesk/app && /opt/helpdesk/venv/bin/python -m app.cli check email' 2>&1 | tail -1)"
fi
if [ -n "${CHECK_AD:-}" ]; then
  checks="$checks\n$(su helpdesk -s /bin/sh -c 'set -a; . /etc/helpdesk/helpdesk.env; set +a; cd /opt/helpdesk/app && /opt/helpdesk/venv/bin/python -m app.cli check directory' 2>&1 | tail -1)"
fi
rm -f "$ANSWERS"
note "Installed. Open https://$HOST/ and sign in as the admin.\n$checks\n\nAnything that didn't connect can be fixed in Settings (Email, Directory), which have the same test buttons."
