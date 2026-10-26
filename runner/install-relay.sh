#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run a Layer 8 relay on a Linux host (the appliance itself, or any Debian/Ubuntu server) as its
# own locked-down service, separate from the service desk.
#
#   sh install-relay.sh --url https://helpdesk.corp.example --token-file /root/relay.token            # preview (default)
#   sh install-relay.sh --url https://helpdesk.corp.example --token-file /root/relay.token --apply    # install / update
#   sh install-relay.sh --status        # service state, last log lines, and a check-in
#   sh install-relay.sh --uninstall     # stop and remove the service, its account and files
#
# The relay runs as the system account layer8-relay. It isn't in the helpdesk group and the service
# can't see the service desk's program, settings, database password, secrets, files or backups:
# systemd hides /opt/helpdesk, /etc/helpdesk, /var/lib/helpdesk and the updater's state from it,
# and the rest of the system is read-only to it. The token is read from a file (never the command
# line), stored root-owned in /etc/layer8-relay/token, readable by layer8-relay only. Running it
# again updates the program and the token in place. Revoking: Settings > Relays > New token (or
# delete the relay); the old token stops working at once.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
USER_NAME=layer8-relay
PROGRAM_DIR=/opt/layer8-relay
CONF_DIR=/etc/layer8-relay
STATE_DIR=/var/lib/layer8-relay
UNIT=/etc/systemd/system/layer8-relay.service
MODE=preview URL="" TOKEN_FILE="" CA_FILE="" EXTRA_DENY=""

usage() { sed -n '3,10p' "$0"; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL=$2; shift ;;
    --token-file) TOKEN_FILE=$2; shift ;;
    --ca-file) CA_FILE=$2; shift ;;
    --deny) EXTRA_DENY="$EXTRA_DENY $2"; shift ;;  # another path to hide from the relay, e.g. a backup mount
    --apply) MODE=apply ;;
    --status) MODE=status ;;
    --uninstall) MODE=uninstall ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }

if [ "$MODE" = status ]; then
  systemctl --no-pager status layer8-relay.service || true
  journalctl --no-pager -u layer8-relay.service -n 20 || true
  if [ -r "$CONF_DIR/relay.conf" ]; then
    . "$CONF_DIR/relay.conf"
    runuser -u "$USER_NAME" -- /usr/bin/python3 -I "$PROGRAM_DIR/helpdesk_runner.py" --url "$RELAY_URL" --token-file "$CONF_DIR/token" ${RELAY_CA:+--ca-file "$RELAY_CA"} --check
  fi
  exit 0
fi

if [ "$MODE" = uninstall ]; then
  [ "$(id -u)" = 0 ] || { echo "Run as root" >&2; exit 1; }
  systemctl disable --now layer8-relay.service 2>/dev/null || true
  rm -f "$UNIT" && systemctl daemon-reload
  rm -rf "$PROGRAM_DIR" "$CONF_DIR" "$STATE_DIR"
  id "$USER_NAME" >/dev/null 2>&1 && userdel "$USER_NAME"
  say "Removed the relay service, its account and files. Delete or revoke it in Settings > Relays too."
  exit 0
fi

[ -n "$URL" ] || { echo "--url is required (the service desk's https:// address)" >&2; exit 2; }
case "$URL" in https://*) ;; *) echo "Use the service desk's https:// address" >&2; exit 2 ;; esac
[ -n "$TOKEN_FILE" ] || { echo "--token-file is required (Settings > Relays > Add a relay, saved to a file)" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 (3.10+) is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "python3 3.10 or later is required" >&2; exit 1; }
[ -f "$HERE/helpdesk_runner.py" ] || { echo "helpdesk_runner.py isn't next to this script" >&2; exit 1; }
[ -z "$CA_FILE" ] || [ -r "$CA_FILE" ] || { echo "Can't read $CA_FILE" >&2; exit 1; }
if [ "$MODE" = apply ]; then
  [ "$(id -u)" = 0 ] || { echo "Run as root to apply" >&2; exit 1; }
  [ -r "$TOKEN_FILE" ] || { echo "Can't read $TOKEN_FILE" >&2; exit 1; }
  TOKEN=$(tr -d ' \r\n' < "$TOKEN_FILE")
  case "$TOKEN" in hdr_*) ;; *) echo "That doesn't look like a relay token (hdr_...)" >&2; exit 1 ;; esac
fi

DENY="/opt/helpdesk /etc/helpdesk /var/lib/helpdesk /var/lib/layer8-updater /usr/local/lib/layer8-updater /opt/helpdesk-setup$EXTRA_DENY"
INACCESSIBLE=""
for p in $DENY; do INACCESSIBLE="$INACCESSIBLE -$p"; done

say "Layer 8 relay: $( [ "$MODE" = apply ] && echo installing || echo 'preview (nothing changes; add --apply)')"
say "  account     $USER_NAME (system, no login, not in the helpdesk group)"
say "  program     $PROGRAM_DIR/helpdesk_runner.py (root-owned)"
say "  token       $CONF_DIR/token (readable by $USER_NAME only)"
say "  work dir    $STATE_DIR"
say "  service     layer8-relay.service -> $URL${CA_FILE:+ (CA $CA_FILE)}"
say "  hidden from it:$DENY"
[ "$MODE" = apply ] || exit 0

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx helpdesk; then
  echo "$USER_NAME is in the helpdesk group; remove it first (gpasswd -d $USER_NAME helpdesk)" >&2; exit 1
fi
install -d -m 755 -o root -g root "$PROGRAM_DIR"
install -m 644 -o root -g root "$HERE/helpdesk_runner.py" "$PROGRAM_DIR/helpdesk_runner.py"
install -d -m 750 -o root -g "$USER_NAME" "$CONF_DIR"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$STATE_DIR"
umask 077
printf '%s\n' "$TOKEN" > "$CONF_DIR/token.new"
chown root:"$USER_NAME" "$CONF_DIR/token.new" && chmod 640 "$CONF_DIR/token.new" && mv -f "$CONF_DIR/token.new" "$CONF_DIR/token"
RELAY_CA=""
if [ -n "$CA_FILE" ]; then
  install -m 644 -o root -g root "$CA_FILE" "$CONF_DIR/ca.pem"
  RELAY_CA="$CONF_DIR/ca.pem"
fi
printf 'RELAY_URL=%s\nRELAY_CA=%s\n' "$URL" "$RELAY_CA" > "$CONF_DIR/relay.conf"
chmod 644 "$CONF_DIR/relay.conf"

cat > "$UNIT" <<EOF
[Unit]
Description=Layer 8 relay (runs scripts for the service desk)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$STATE_DIR
ExecStart=/usr/bin/python3 -I $PROGRAM_DIR/helpdesk_runner.py --url $URL --token-file $CONF_DIR/token${RELAY_CA:+ --ca-file $RELAY_CA}
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=$STATE_DIR
InaccessiblePaths=$INACCESSIBLE

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
# Prove the address, certificate and token before leaving it running (claims no work).
if ! runuser -u "$USER_NAME" -- /usr/bin/python3 -I "$PROGRAM_DIR/helpdesk_runner.py" --url "$URL" --token-file "$CONF_DIR/token" ${RELAY_CA:+--ca-file "$RELAY_CA"} --check; then
  echo "Check-in failed: the service isn't started. Fix the address, certificate or token and run again." >&2
  exit 1
fi
systemctl enable --now layer8-relay.service >/dev/null
systemctl restart layer8-relay.service
say "Running. Status and logs: sh $0 --status"
