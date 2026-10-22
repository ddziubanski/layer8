#!/bin/sh
# Runs inside a debian:trixie container (iso/build.sh starts it): Debian's netinst ISO plus the
# service desk bundle and an automated install entry, repacked with the same BIOS and UEFI boot.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq --no-install-recommends xorriso >/dev/null
SRC=$1 OUT=$2
ADD=/tmp/add
rm -rf "$ADD" && mkdir -p "$ADD/helpdesk"
cp /src/iso/preseed.cfg "$ADD/preseed.cfg"
cp -a /out/bundle "$ADD/helpdesk/bundle"
[ -f /out/answers.json ] && cp /out/answers.json "$ADD/helpdesk/answers.json"

# Boot menus: one entry only, on BIOS (isolinux) and UEFI (grub). No Debian installer choices
# ("Graphical install", expert, rescue...): picking one of those would install plain Debian with its
# own defaults (a desktop) and no service desk. To install Debian by hand, use Debian's own ISO.
ARGS="auto=true priority=critical preseed/file=/cdrom/preseed.cfg vga=788 --- quiet console=tty0 console=ttyS0,115200n8"
mkdir -p "$ADD/isolinux" "$ADD/boot/grub"
printf 'default helpdesk\nprompt 1\ntimeout 50\nsay Layer 8: installing in 5 seconds (erases the first disk)...\nlabel helpdesk\n\tkernel /install.amd/vmlinuz\n\tappend initrd=/install.amd/initrd.gz %s\n' "$ARGS" > "$ADD/isolinux/isolinux.cfg"
printf 'set default=0\nset timeout=5\nmenuentry "Install Layer 8 (erases the disk)" {\n\tlinux /install.amd/vmlinuz %s\n\tinitrd /install.amd/initrd.gz\n}\n' "$ARGS" > "$ADD/boot/grub/grub.cfg"

xorriso -indev "$SRC" -outdev "$OUT" -map "$ADD" / -boot_image any replay -compliance no_emul_toc >/dev/null 2>&1
ls -la "$OUT"
