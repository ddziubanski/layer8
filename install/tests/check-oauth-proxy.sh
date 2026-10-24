#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Exercise the shipped proxy configuration with a synthetic callback, no provider.
set -eu
qa_dir=$(mktemp -d)
trap 'docker rm -f layer8-oauth-proxy-test >/dev/null 2>&1 || true' EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -keyout "$qa_dir/key.pem" -out "$qa_dir/cert.pem" 2>/dev/null
sed 's/@HOST@/localhost/g' install/files/nginx-helpdesk.conf > "$qa_dir/server.conf"
printf 'events {}\nhttp { include /etc/nginx/layer8-server.conf; }\n' > "$qa_dir/nginx.conf"
docker run --detach --name layer8-oauth-proxy-test \
    --publish 127.0.0.1:8443:443 \
    --volume "$qa_dir:/etc/helpdesk/tls:ro" \
    --volume "$qa_dir/server.conf:/etc/nginx/layer8-server.conf:ro" \
    --volume "$qa_dir/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:stable
# No backend is running. Even an upstream failure must not leak callback query
# parameters through access/error logs or inherited same-origin referrer headers.
attempt=0
until curl --silent --show-error --insecure --connect-timeout 1 \
    --output /dev/null --dump-header "$qa_dir/headers" \
    'https://127.0.0.1:8443/api/meraki/oauth/callback?code=synthetic-proxy-code&state=example-state'; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 12 ]
    sleep 1
done
docker logs layer8-oauth-proxy-test > "$qa_dir/proxy.log" 2>&1
python3 - "$qa_dir" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
headers = (root / "headers").read_text().lower().splitlines()
assert any(line.startswith("http/") and "502" in line for line in headers), headers
assert [line for line in headers if line.startswith("referrer-policy:")] == ["referrer-policy: no-referrer"], headers
assert "cache-control: no-store" in headers, headers
assert "x-content-type-options: nosniff" in headers, headers
assert any(line.startswith("strict-transport-security:") for line in headers), headers
assert "synthetic-proxy-code" not in (root / "proxy.log").read_text(), "Callback code leaked into proxy logs"
print("Nginx OAuth callback headers and access/error log redaction passed")
PY
