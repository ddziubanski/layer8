#!/bin/sh
# Layer 8 installer for a Debian 13 server (the appliance ISO runs this too).
#
#   sudo ./install/install.sh --answers answers.json     # from the setup wizard, or written by hand
#   sudo ./install/install.sh --demo                     # offline demo: sample data, self-signed TLS
#
# Run from a release bundle (or a checkout): backend/, frontend/dist/, runner/, tools/, install/,
# and for offline installs wheels/ (Python packages) and debs/ (Debian packages). Safe to run again:
# it updates the app, keeps the database, settings, files and secrets, and runs the migrations.
#
# The answers file is what `python -m app.cli setup` takes (settings sections, see
# docs/install.md), plus what only the installer needs, in "install":
#   "install": {"hostname": "helpdesk.corp.example",
#               "tls": "self-signed" | {"cert": "/path/cert.pem", "key": "/path/key.pem"},
#               "admin": {"email": ..., "name": ..., "password": ...},
#               "demo": false}
set -eu

BUNDLE=$(cd "$(dirname "$0")/.." && pwd)
ANSWERS=""
DEMO=0
SERVICES=1
while [ $# -gt 0 ]; do
  case "$1" in
    --answers) ANSWERS=$2; shift 2 ;;
    --demo) DEMO=1; shift ;;
    # For testing in a container without systemd: configure everything but start nothing.
    --no-services) SERVICES=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root (sudo)"

# --- What we're installing on

. /etc/os-release
case "${ID}:${VERSION_ID:-}" in
  debian:13) ;;
  *) echo "Warning: tested on Debian 13; this is ${PRETTY_NAME}. Continuing." >&2 ;;
esac

APP=/opt/helpdesk
DATA=/var/lib/helpdesk
ETC=/etc/helpdesk
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ "$DEMO" = 1 ] && [ -z "$ANSWERS" ]; then
  ANSWERS=$WORK/answers.json
  cat > "$ANSWERS" <<JSON
{"install": {"hostname": "$(hostname -f 2>/dev/null || hostname)", "tls": "self-signed", "demo": true}}
JSON
fi
[ -n "$ANSWERS" ] || die "give --answers FILE (the setup wizard writes one) or --demo"
[ -f "$ANSWERS" ] || die "no such answers file: $ANSWERS"

# Read "install" values with the system Python (always there on Debian).
ans() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get("install",{})
for k in sys.argv[2].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print("" if v is None else (v if isinstance(v, str) else json.dumps(v)))' "$ANSWERS" "$1"; }
HOST=$(ans hostname); HOST=${HOST:-$(hostname -f 2>/dev/null || hostname)}
TLS=$(ans tls); TLS=${TLS:-self-signed}
# A real install names its first admin: a service desk with no admin would let whoever reaches it
# first become one (admin / admin). Only the offline demo, which has its own admin, goes without.
if [ "$(ans demo)" != true ] && [ -z "$(ans admin.password)" ]; then
  die "the answers file needs install.admin (email, name and password) for the first admin"
fi

# --- Time zone (backup times and scheduled reports follow it)

TZ_WANTED=$(ans timezone)
if [ -n "$TZ_WANTED" ] && [ -f "/usr/share/zoneinfo/$TZ_WANTED" ]; then
  timedatectl set-timezone "$TZ_WANTED" 2>/dev/null || { ln -sf "/usr/share/zoneinfo/$TZ_WANTED" /etc/localtime; echo "$TZ_WANTED" > /etc/timezone; }
fi

# --- Packages

say "Installing packages"
PKGS="postgresql postgresql-client nginx python3 python3-venv libkrb5-3 libgssapi-krb5-2 krb5-user openssl sudo cifs-utils nfs-common rsync ca-certificates whiptail curl ifupdown openssh-server"
export DEBIAN_FRONTEND=noninteractive
if [ -d "$BUNDLE/debs" ] && ls "$BUNDLE"/debs/*.deb >/dev/null 2>&1; then
  # Offline: everything comes from the bundle, nothing from the internet.
  apt-get install -y -q --no-install-recommends "$BUNDLE"/debs/*.deb >/dev/null
else
  apt-get update -q >/dev/null
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $PKGS >/dev/null
fi

# --- Account and folders

say "Service account and folders"
id helpdesk >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin helpdesk
install -d -m 755 "$APP"
install -d -m 750 -o helpdesk -g helpdesk "$DATA" "$DATA/storage"
install -d -m 700 -o helpdesk -g helpdesk "$DATA/secrets"
install -d -m 750 -o root -g helpdesk "$ETC" "$ETC/tls"

# --- The app

say "App files"
rsync -a --delete --exclude .venv --exclude storage --exclude secrets --exclude __pycache__ --exclude '*.egg-info' --exclude tests \
  "$BUNDLE/backend/" "$APP/app/"
[ -d "$BUNDLE/frontend/dist" ] || die "no built web UI in $BUNDLE/frontend/dist (run npm run build there first)"
rsync -a --delete "$BUNDLE/frontend/dist/" "$APP/web/"
for extra in runner tools docs; do
  [ -d "$BUNDLE/$extra" ] && rsync -a --delete "$BUNDLE/$extra/" "$APP/$extra/"
done
[ -f "$BUNDLE/LICENSE" ] && cp "$BUNDLE/LICENSE" "$APP/"
chown -R root:root "$APP"

say "Python environment"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
if [ -d "$BUNDLE/wheels" ]; then
  "$APP/venv/bin/pip" install -q --no-index --find-links "$BUNDLE/wheels" "$APP/app"
else
  # Online: build what has no ready-made wheel (gssapi) here. The ISO carries built wheels instead.
  apt-get install -y -q --no-install-recommends gcc python3-dev libkrb5-dev >/dev/null
  "$APP/venv/bin/pip" install -q --upgrade pip
  "$APP/venv/bin/pip" install -q "$APP/app"
fi

# --- Database

say "Database"
systemctl start postgresql 2>/dev/null || pg_ctlcluster "$(ls /etc/postgresql | sort -n | tail -1)" main start 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do su postgres -c "psql -qtAc 'select 1'" >/dev/null 2>&1 && break; sleep 1; done
ENV=$ETC/helpdesk.env
if [ -f "$ENV" ]; then
  echo "Keeping the database logins in $ENV"
else
  APP_PW=$(openssl rand -hex 24)
  RULES_PW=$(openssl rand -hex 24)
  su postgres -c "psql -q -v ON_ERROR_STOP=1" <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'helpdesk') THEN CREATE ROLE helpdesk LOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'helpdesk_rules') THEN CREATE ROLE helpdesk_rules LOGIN; END IF;
END \$\$;
ALTER ROLE helpdesk LOGIN PASSWORD '$APP_PW';
ALTER ROLE helpdesk_rules LOGIN PASSWORD '$RULES_PW';
SQL
  su postgres -c "psql -qtAc \"SELECT 1 FROM pg_database WHERE datname = 'helpdesk'\"" | grep -q 1 || su postgres -c "createdb -O helpdesk helpdesk"
  umask 027
  cat > "$ENV" <<ENVFILE
# Bootstrap settings (everything else is in the app: Settings). Written by install.sh.
HELPDESK_DATABASE_URL=postgresql+psycopg://helpdesk:$APP_PW@localhost:5432/helpdesk
HELPDESK_RULES_DATABASE_URL=postgresql+psycopg://helpdesk_rules:$RULES_PW@localhost:5432/helpdesk
HELPDESK_STORAGE_DIR=$DATA/storage
HELPDESK_SECRETS_DIR=$DATA/secrets
HELPDESK_FRONTEND_DIR=$APP/web
HELPDESK_WORKER_RESTART_COMMAND="sudo -n /usr/bin/systemctl restart helpdesk-worker"
ENVFILE
  chown root:helpdesk "$ENV"
  chmod 640 "$ENV"
fi

# Run as the service account, with its settings.
as_app() { su helpdesk -s /bin/sh -c "set -a; . $ENV; set +a; cd $APP/app && $*"; }

say "Database schema"
# sh has no pipefail: keep alembic's own exit status, so a failed migration (or two migration
# heads) stops the install instead of carrying on with an unmigrated database.
MIGRATE_LOG=$(mktemp)
if as_app "$APP/venv/bin/alembic upgrade head" >"$MIGRATE_LOG" 2>&1; then migrated=0; else migrated=$?; fi
grep -v "^INFO  \[alembic.runtime.migration\] \(Context impl\|Will assume\)" "$MIGRATE_LOG" || true
rm -f "$MIGRATE_LOG"
[ "$migrated" -eq 0 ] || die "the database schema update failed (alembic exit $migrated); nothing after it was changed. See the output above."

# --- Backup share (optional): mounted at /mnt/helpdesk-backup for Settings > Backup

KIND=$(ans backup.kind)
if [ -n "$KIND" ]; then
  say "Backup share ($KIND)"
  MNT=/mnt/helpdesk-backup
  install -d -m 700 -o helpdesk -g helpdesk "$MNT"
  SHARE=$(ans backup.share)
  sed -i '\#[[:space:]]/mnt/helpdesk-backup[[:space:]]#d' /etc/fstab
  if [ "$KIND" = smb ]; then
    CRED=$ETC/backup-share.cred
    USERNAME=$(ans backup.user)
    DOMAIN=""
    case "$USERNAME" in *\\*) DOMAIN=${USERNAME%%\\*}; USERNAME=${USERNAME#*\\} ;; esac
    umask 077
    { echo "username=$USERNAME"; echo "password=$(ans backup.password)"; [ -n "$DOMAIN" ] && echo "domain=$DOMAIN"; } > "$CRED"
    umask 022
    echo "$SHARE $MNT cifs credentials=$CRED,uid=helpdesk,gid=helpdesk,file_mode=0600,dir_mode=0700,vers=3.0,_netdev,nofail 0 0" >> /etc/fstab
  else
    echo "$SHARE $MNT nfs _netdev,nofail 0 0" >> /etc/fstab
  fi
  mount "$MNT" 2>/dev/null && echo "Mounted $SHARE at $MNT" || echo "Couldn't mount $SHARE yet; it's in /etc/fstab and is tried at every boot. Settings > Backup shows whether the folder is there."
fi

say "Settings, first admin$( [ "$(ans demo)" = true ] && echo ', offline demo')"
# As root, so {"file": ...} paths in the answers file work wherever it is; the files it writes
# (secrets, branding images) are then handed to the service account.
ANSWERS_ABS=$(cd "$(dirname "$ANSWERS")" && pwd)/$(basename "$ANSWERS")
( set -a; . "$ENV"; set +a; cd "$APP/app" && "$APP/venv/bin/python" -m app.cli seed && "$APP/venv/bin/python" -m app.cli setup "$ANSWERS_ABS" )
chown -R helpdesk:helpdesk "$DATA"
chmod 700 "$DATA/secrets"

# --- HTTPS

say "HTTPS for $HOST"
if [ "$TLS" = "self-signed" ]; then
  # A self-signed one is (re)made when there's none yet or it's for another name.
  if [ ! -f "$ETC/tls/cert.pem" ] || ! openssl x509 -in "$ETC/tls/cert.pem" -noout -checkhost "$HOST" 2>/dev/null | grep -q "does match"; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 1825 -subj "/CN=$HOST" \
      -addext "subjectAltName=DNS:$HOST" -keyout "$ETC/tls/key.pem" -out "$ETC/tls/cert.pem" 2>/dev/null
    echo "Self-signed certificate made; replace it with one your PCs trust (docs/install.md)."
  fi
else
  install -m 644 "$(ans tls.cert)" "$ETC/tls/cert.pem"
  install -m 600 "$(ans tls.key)" "$ETC/tls/key.pem"
fi
chmod 600 "$ETC/tls/key.pem"
sed "s/@HOST@/$HOST/" "$BUNDLE/install/files/nginx-helpdesk.conf" > /etc/nginx/sites-available/helpdesk
ln -sf /etc/nginx/sites-available/helpdesk /etc/nginx/sites-enabled/helpdesk
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tail -1

# --- Services

say "Services"
install -m 644 "$BUNDLE/install/files/helpdesk-api.service" "$BUNDLE/install/files/helpdesk-worker.service" /etc/systemd/system/
install -m 440 "$BUNDLE/install/files/sudoers-helpdesk" /etc/sudoers.d/helpdesk
if [ "$SERVICES" = 1 ]; then
  systemctl daemon-reload
  systemctl enable --now postgresql helpdesk-api helpdesk-worker nginx >/dev/null 2>&1
  systemctl restart helpdesk-api helpdesk-worker nginx
else
  echo "(--no-services: not starting anything)"
fi

# --- Recovery console

say "Recovery console (account layer8)"
install -m 755 -o root -g root "$BUNDLE/install/files/layer8-recovery" /usr/local/sbin/layer8-recovery
install -m 755 -o root -g root "$BUNDLE/install/files/layer8-console" /usr/local/sbin/layer8-console
grep -qx /usr/local/sbin/layer8-console /etc/shells || echo /usr/local/sbin/layer8-console >> /etc/shells
id layer8 >/dev/null 2>&1 || useradd --create-home --shell /usr/local/sbin/layer8-console --comment "Layer 8 recovery console" layer8
usermod --shell /usr/local/sbin/layer8-console layer8
RECOVERY_PW=$(ans recovery.password)
if [ -n "$RECOVERY_PW" ]; then
  printf 'layer8:%s\n' "$RECOVERY_PW" | chpasswd
elif ! passwd -S layer8 | grep -q ' P '; then
  # No password chosen: locked, never a default (anyone on the network could log in first).
  passwd -l layer8 >/dev/null
  echo "Recovery console: no password given (install.recovery.password), so the layer8 account stays locked."
fi
# The OS administrator (sdadmin, made locked by the ISO): its password, if one was chosen.
OS_PW=$(ans os_password)
if id sdadmin >/dev/null 2>&1; then
  if [ -n "$OS_PW" ]; then
    printf 'sdadmin:%s\n' "$OS_PW" | chpasswd
  elif ! passwd -S sdadmin | grep -q ' P '; then
    echo "OS administrator: no password given (install.os_password), so sdadmin stays locked."
  fi
fi
# Only the helper's fixed commands, nothing else, with no password (the login was the check).
install -m 440 "$BUNDLE/install/files/sudoers-layer8" /etc/sudoers.d/layer8-recovery
visudo -cf /etc/sudoers.d/layer8-recovery >/dev/null || { rm -f /etc/sudoers.d/layer8-recovery; die "sudoers check failed for layer8-recovery"; }
# Over SSH too, but only the menu: no other command, forwarding or file transfer.
install -d /etc/ssh/sshd_config.d
install -m 644 "$BUNDLE/install/files/sshd-layer8.conf" /etc/ssh/sshd_config.d/layer8.conf
if command -v sshd >/dev/null && sshd -t 2>/dev/null; then
  [ "$SERVICES" = 1 ] && systemctl reload ssh 2>/dev/null || true
else
  echo "(sshd not installed or its config didn't check; the console still works locally)"
fi

touch "$DATA/.installed"
# The answers file holds every password given at install: gone once they're applied.
case "$ANSWERS" in
  /opt/helpdesk-setup/*) shred -u "$ANSWERS" 2>/dev/null || rm -f "$ANSWERS" ;;
esac
# Flush everything to disk: a VM switched off right after setup must still boot into a working app.
sync

say "Done"
echo "Open https://$HOST/"
if [ "$(ans demo)" = true ]; then
  echo "Offline demo: sign in as admin@demo.local with the password on the sign-in page."
fi
