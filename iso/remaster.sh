#!/bin/sh
# Runs inside a debian:trixie container (iso/build.sh starts it): Debian's netinst ISO plus the
# service desk bundle and an automated install entry, repacked with the same BIOS and UEFI boot.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq --no-install-recommends xorriso python3 isolinux syslinux-common >/dev/null
SRC=$1 OUT=$2
ADD=/tmp/add
rm -rf "$ADD" && mkdir -p "$ADD/helpdesk"
# iso/build.sh --test: serial console and an unattended ERASE, for dev/iso-test.sh only.
python3 /src/iso/prepare-media.py /src/iso "$ADD" $([ -f /out/test-media ] && echo --test)
# Ship the menu and its matching modules together, including text-menu fallback.
cp /usr/lib/syslinux/modules/bios/vesamenu.c32 /usr/lib/syslinux/modules/bios/libcom32.c32 /usr/lib/syslinux/modules/bios/libutil.c32 "$ADD/isolinux/"
cp -a /out/bundle "$ADD/helpdesk/bundle"
[ -f /out/answers.json ] && cp /out/answers.json "$ADD/helpdesk/answers.json"

# Both menus wait for a choice, including ISOs carrying unattended answers.

xorriso -indev "$SRC" -outdev "$OUT" -map "$ADD" / -boot_image any replay -compliance no_emul_toc >/dev/null 2>&1
ls -la "$OUT"
