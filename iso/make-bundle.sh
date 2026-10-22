#!/bin/sh
# Runs inside a debian:trixie container (iso/build.sh starts it): makes the offline bundle the
# ISO carries in /out/bundle: the app, the built web UI, Python wheels for Debian 13's Python
# (gssapi built here, so the appliance needs no compiler), and the Debian packages the installer
# adds to a base system.
set -eu
export DEBIAN_FRONTEND=noninteractive
B=/out/bundle
rm -rf "$B" && mkdir -p "$B/debs" "$B/wheels"
apt-get update -qq

# Packages first, in this still-minimal container: every package (with dependencies) a base
# Debian install doesn't already have. Keep in step with PKGS in install/install.sh.
PKGS="postgresql postgresql-client nginx python3 python3-venv libkrb5-3 libgssapi-krb5-2 krb5-user openssl sudo cifs-utils nfs-common rsync ca-certificates whiptail curl ifupdown openssh-server"
mkdir -p /tmp/debs/partial
apt-get install -y -qq --download-only --no-install-recommends -o Dir::Cache::Archives=/tmp/debs $PKGS >/dev/null
cp /tmp/debs/*.deb "$B/debs/"
echo "debs: $(ls "$B/debs" | wc -l)"

# Wheels: built against Debian 13's Python.
apt-get install -y -qq --no-install-recommends python3 python3-venv python3-dev gcc libkrb5-dev rsync >/dev/null
python3 -m venv /tmp/v
/tmp/v/bin/pip install -q --upgrade pip
rsync -a --exclude .venv --exclude storage --exclude secrets --exclude __pycache__ --exclude '*.egg-info' /src/backend/ /tmp/backend/
cp /src/LICENSE /tmp/LICENSE
/tmp/v/bin/pip wheel -q --wheel-dir "$B/wheels" /tmp/backend
# What pip needs to install the app itself from source with no network.
/tmp/v/bin/pip download -q --dest "$B/wheels" --only-binary=:all: "setuptools>=69" wheel pip
echo "wheels: $(ls "$B/wheels" | wc -l)"

# The app (install.sh copies from here).
rsync -a --exclude .venv --exclude storage --exclude secrets --exclude __pycache__ --exclude '*.egg-info' --exclude tests /src/backend/ "$B/backend/"
mkdir -p "$B/frontend" && rsync -a /src/frontend/dist/ "$B/frontend/dist/"
for d in runner tools docs install; do rsync -a "/src/$d/" "$B/$d/"; done
cp /src/LICENSE "$B/"
cp /out/VERSION "$B/VERSION" 2>/dev/null || date +%Y%m%d > "$B/VERSION"
echo "bundle: $(du -sh "$B" | cut -f1)"
