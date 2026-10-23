#!/bin/sh
# Build the Layer 8 appliance ISO: Debian 13 netinst + the service desk (offline bundle) +
# an automated install that sets up the service desk on first boot.
#
#   iso/build.sh                        # -> iso/build/layer8-<version>.iso (wizard on first boot)
#   iso/build.sh --answers a.json       # unattended: first boot installs with these answers
#   iso/build.sh --demo                 # unattended offline demo
#   iso/build.sh --demo --test          # disposable-VM tests only: serial console, ERASE after 5 s
#
# Needs docker and npm on the build machine, and the internet once (Debian ISO, packages, wheels).
# The ISO then installs with no network at all.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/iso/build
mkdir -p "$OUT"
rm -f "$OUT/answers.json" "$OUT/test-media"
while [ $# -gt 0 ]; do
  case "$1" in
    --answers) cp "$2" "$OUT/answers.json"; chmod 600 "$OUT/answers.json"; shift ;;
    --demo) echo '{"install": {"hostname": "helpdesk", "tls": "self-signed", "demo": true}}' > "$OUT/answers.json" ;;
    --test) touch "$OUT/test-media" ;;
    *) sed -n '2,13p' "$0"; exit 2 ;;
  esac
  shift
done

VERSION=$(git -C "$ROOT" describe --always --dirty 2>/dev/null || date +%Y%m%d)
echo "$VERSION" > "$OUT/VERSION"

# Debian's netinst ISO (the current 13.x point release), once.
if ! ls "$OUT"/debian-13.*-amd64-netinst.iso >/dev/null 2>&1; then
  BASE=https://cdimage.debian.org/debian-cd/current/amd64/iso-cd
  NAME=$(curl -fsSL "$BASE/" | grep -o 'debian-13\.[0-9.]*-amd64-netinst\.iso' | head -1)
  echo "Downloading $NAME"
  curl -fL --progress-bar -o "$OUT/$NAME" "$BASE/$NAME"
  curl -fsSL "$BASE/SHA256SUMS" | grep " $NAME\$" | (cd "$OUT" && sha256sum -c -)
fi
DEBIAN_ISO=$(ls "$OUT"/debian-13.*-amd64-netinst.iso | sort | tail -1)

echo "== Web UI"
(cd "$ROOT/frontend" && npm ci --silent && npm run -s build)

echo "== Offline bundle (Debian 13 container)"
docker run --rm -v "$ROOT":/src:ro -v "$OUT":/out debian:trixie sh /src/iso/make-bundle.sh

# Never ship customer data: the bundle may hold only the app, the UI, wheels and Debian packages.
# Database backups or dumps, or any large file outside debs/ and wheels/, stop the build.
LEAK=$(find "$OUT/bundle" -type f \( -iname '*.bak' -o -iname '*.mdf' -o -iname '*.ldf' -o -iname '*.dump' -o -iname '*.sql.gz' -o -iname '*.db' -o -iname '*.sqlite*' \) 2>/dev/null)
BIG=$(find "$OUT/bundle" -type f -size +20M ! -path "$OUT/bundle/debs/*" ! -path "$OUT/bundle/wheels/*" 2>/dev/null)
if [ -n "$LEAK$BIG" ]; then
  echo "Refusing to build: the bundle holds files that look like data:" >&2
  printf '%s\n' $LEAK $BIG >&2
  exit 1
fi

echo "== ISO"
ISO=layer8-$VERSION.iso
docker run --rm -v "$ROOT":/src:ro -v "$OUT":/out debian:trixie sh /src/iso/remaster.sh "/out/$(basename "$DEBIAN_ISO")" "/out/$ISO"
# The container ran as root: hand the output back.
docker run --rm -v "$OUT":/out debian:trixie chown -R "$(id -u):$(id -g)" /out
echo "Built $OUT/$ISO"
