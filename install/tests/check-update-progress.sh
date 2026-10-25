#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Real Nginx, no API process: progress must work while all ordinary access is gated.
set -eu
qa_dir=$(mktemp -d)
trap 'docker rm -f layer8-update-proxy-test >/dev/null 2>&1 || true' EXIT
chmod 755 "$qa_dir"
mkdir -p "$qa_dir/update/progress"
chmod 755 "$qa_dir/update" "$qa_dir/update/progress"
printf '{"phase":"migrating","step":5,"steps":9,"message":"Applying schema"}\n' > "$qa_dir/update/progress/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
chmod 644 "$qa_dir/update/progress/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
touch "$qa_dir/update/maintenance"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -keyout "$qa_dir/key.pem" -out "$qa_dir/cert.pem" 2>/dev/null
sed 's/@HOST@/localhost/g' install/files/nginx-helpdesk.conf > "$qa_dir/server.conf"
printf 'events {}\nhttp { include /etc/nginx/layer8-server.conf; }\n' > "$qa_dir/nginx.conf"
docker run --detach --name layer8-update-proxy-test \
    --publish 127.0.0.1:8444:443 \
    --volume "$qa_dir:/etc/helpdesk/tls:ro" \
    --volume "$qa_dir/update:/var/lib/layer8-updater:ro" \
    --volume "$qa_dir/server.conf:/etc/nginx/layer8-server.conf:ro" \
    --volume "$qa_dir/nginx.conf:/etc/nginx/nginx.conf:ro" \
    --volume "$PWD/install/files/maintenance.html:/usr/local/lib/layer8-updater/maintenance.html:ro" nginx:stable
attempt=0
until curl --silent --insecure --connect-timeout 1 --output /dev/null 'https://127.0.0.1:8444/'; do
    attempt=$((attempt + 1)); [ "$attempt" -lt 12 ]; sleep 1
done
curl --silent --show-error --insecure --dump-header "$qa_dir/progress.headers" \
    'https://127.0.0.1:8444/api/update-progress/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json' > "$qa_dir/progress.body"
curl --silent --insecure --dump-header "$qa_dir/maintenance.headers" 'https://127.0.0.1:8444/' > "$qa_dir/maintenance.body"
for endpoint in / /api/health /api/source '/api/meraki/oauth/callback?code=synthetic-update-code'; do
    code=$(curl --silent --insecure --output /dev/null --write-out '%{http_code}' "https://127.0.0.1:8444$endpoint")
    [ "$code" = 503 ] || { echo "Maintenance leaked $endpoint: $code" >&2; exit 1; }
done
code=$(curl --silent --insecure --request POST --output /dev/null --write-out '%{http_code}' 'https://127.0.0.1:8444/api/update-progress/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json')
[ "$code" = 403 ]
code=$(curl --silent --insecure --output /dev/null --write-out '%{http_code}' 'https://127.0.0.1:8444/api/update-progress/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json')
[ "$code" = 404 ]
docker logs layer8-update-proxy-test > "$qa_dir/proxy.log" 2>&1
python3 - "$qa_dir" <<'PY'
from pathlib import Path
import json, sys
root = Path(sys.argv[1])
headers = (root/'progress.headers').read_text().lower()
assert '200' in headers.splitlines()[0], headers
maintenance_headers = (root/'maintenance.headers').read_text().lower()
assert '503' in maintenance_headers.splitlines()[0]
assert 'cache-control: no-store' in maintenance_headers and 'referrer-policy: no-referrer' in maintenance_headers
maintenance = (root/'maintenance.body').read_text()
assert 'Reconnecting to your service desk' in maintenance
assert "fetch('/api/health'" in maintenance and "fetch('/api/about'" in maintenance
assert 'window.location.reload()' in maintenance
assert 'cache-control: no-store' in headers
assert 'referrer-policy: no-referrer' in headers
assert json.loads((root/'progress.body').read_text())['phase'] == 'migrating'
logs = (root/'proxy.log').read_text()
assert 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' not in logs, 'Progress capability leaked to logs'
assert 'synthetic-update-code' not in logs, 'OAuth code leaked during maintenance'
print('Live Nginx maintenance/progress isolation, headers, methods and log redaction passed')
PY
