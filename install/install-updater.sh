#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# One-time bootstrap for an existing Debian appliance. Run from a reviewed release
# containing the updater API/UI; this never installs an app release or trust key.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
[ "$#" = 0 ] || { echo 'This bootstrap takes no arguments' >&2; exit 1; }
BUNDLE=$(cd "$(dirname "$0")/.." && pwd)
id helpdesk >/dev/null 2>&1
install -d -m 755 -o root -g root /usr/local/lib/layer8-updater /etc/helpdesk/update-keys /var/lib/layer8-updater
install -d -m 711 -o root -g root /var/lib/layer8-updater/work
install -d -m 755 -o root -g root /var/lib/layer8-updater/progress
install -d -m 770 -o root -g helpdesk /var/lib/layer8-updater/inbox
install -m 644 -o root -g root "$BUNDLE/backend/app/update_protocol.py" /usr/local/lib/layer8-updater/protocol.py
install -m 644 -o root -g root "$BUNDLE/install/files/layer8-update.py" /usr/local/lib/layer8-updater/
install -m 644 -o root -g root "$BUNDLE/install/files/layer8-update.service" /etc/systemd/system/
printf '%s\n' 'helpdesk ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block layer8-update.service' > /etc/sudoers.d/layer8-update
chmod 440 /etc/sudoers.d/layer8-update
visudo -cf /etc/sudoers.d/layer8-update >/dev/null
# Preserve the site's TLS/host/proxy configuration. Add just the fixed gate to
# the known appliance server block, refusing an unfamiliar layout.
python3 - "$BUNDLE/install/files/nginx-helpdesk.conf" <<'PY'
from pathlib import Path
import sys
path = Path('/etc/nginx/sites-available/helpdesk')
text = path.read_text()
if '/var/lib/layer8-updater/maintenance' not in text:
    marker = '    # Attachments:'
    template = Path(sys.argv[1]).read_text()
    gate = template[template.index('    # Only the root-owned updater'):template.index(marker)]
    if text.count(marker) != 1 or 'proxy_pass http://127.0.0.1:8000;' not in text:
        raise SystemExit('Unknown Nginx layout; add the maintenance gate manually before enabling updates')
    backup = path.with_name('helpdesk.before-updater')
    if backup.exists():
        raise SystemExit('A previous bootstrap backup exists; inspect locally before retrying')
    backup.write_text(text)
    text = text.replace(marker, gate + marker, 1)
    for location in ('    location = /api/meraki/oauth/callback {\n', '    location / {\n'):
        if text.count(location) != 1:
            raise SystemExit('Unknown proxy locations; add the maintenance gate manually')
        text = text.replace(location, location + '        if ($layer8_maintenance = 1) { return 503 "Layer 8 update maintenance. Please keep the Updates tab open.\\n"; }\n', 1)
    path.write_text(text)
PY
if ! nginx -t; then
  if [ -f /etc/nginx/sites-available/helpdesk.before-updater ]; then
    cp /etc/nginx/sites-available/helpdesk.before-updater /etc/nginx/sites-available/helpdesk
  fi
  echo 'Nginx check failed; original configuration restored. Do not enable updates.' >&2
  exit 1
fi
systemctl daemon-reload
systemctl reload nginx
echo 'Updater installed. Provision an authenticated Ed25519 public key locally; then enable Settings > Updates.'
