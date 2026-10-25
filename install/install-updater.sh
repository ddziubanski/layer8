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
install -m 644 -o root -g root "$BUNDLE/install/files/maintenance.html" /usr/local/lib/layer8-updater/maintenance.html
install -m 644 -o root -g root "$BUNDLE/install/files/layer8-update.service" /etc/systemd/system/
# The Layer 8 publisher's public key for signed updates (not a secret), shipped with this release.
if [ -f "$BUNDLE/install/files/layer8-update-public.pem" ]; then
  install -m 644 -o root -g root "$BUNDLE/install/files/layer8-update-public.pem" /etc/helpdesk/update-keys/layer8.pem
  echo "Update signing key: $(openssl pkey -pubin -in /etc/helpdesk/update-keys/layer8.pem -outform DER | sha256sum | cut -d' ' -f1)"
fi
printf '%s\n' 'helpdesk ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block layer8-update.service' > /etc/sudoers.d/layer8-update
chmod 440 /etc/sudoers.d/layer8-update
visudo -cf /etc/sudoers.d/layer8-update >/dev/null
# Preserve the site's TLS/host/proxy configuration. Add just the fixed gate to
# the known appliance server block, refusing an unfamiliar layout.
proxy_changed=$(python3 "$BUNDLE/install/files/layer8-proxy-upgrade.py" "$BUNDLE/install/files/nginx-helpdesk.conf")
if ! nginx -t; then
  if [ "$proxy_changed" = 1 ]; then
    cp /etc/nginx/sites-available/helpdesk.before-recovery-page /etc/nginx/sites-available/helpdesk
  fi
  echo 'Nginx check failed; original configuration restored. Do not enable updates.' >&2
  exit 1
fi
systemctl daemon-reload
systemctl reload nginx
echo 'Updater installed. Compare the signing key fingerprint above with docs/github-updates.md; then set a backup passphrase and enable Settings > Updates.'
