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

# Boot menus: our entry first and the default, on BIOS (isolinux) and UEFI (grub).
xorriso -osirrox on -indev "$SRC" -extract /isolinux/txt.cfg /tmp/txt.cfg -extract /boot/grub/grub.cfg /tmp/grub.cfg -extract /isolinux/menu.cfg /tmp/menu.cfg >/dev/null 2>&1
ARGS="auto=true priority=critical preseed/file=/cdrom/preseed.cfg vga=788 --- quiet console=tty0 console=ttyS0,115200n8"
mkdir -p "$ADD/isolinux" "$ADD/boot/grub"
{ printf 'default helpdesk\nlabel helpdesk\n\tmenu label ^Install Layer 8 (erases the disk)\n\tmenu default\n\tkernel /install.amd/vmlinuz\n\tappend initrd=/install.amd/initrd.gz %s\n' "$ARGS"; sed 's/^\tmenu default$//' /tmp/txt.cfg; } > "$ADD/isolinux/txt.cfg"
# BIOS: boot the default entry (ours) after 5 s. Debian's menu instead starts speech synthesis when
# nobody presses a key; its entries stay under Advanced options. Wide enough for our label.
sed -e '/^include spkgtk.cfg$/d' -e '/^include spk.cfg$/d' -e 's/^menu width .*/menu width 78/' /tmp/menu.cfg > "$ADD/isolinux/menu.cfg"
printf 'path \ninclude menu.cfg\ndefault vesamenu.c32\nprompt 0\ntimeout 50\n' > "$ADD/isolinux/isolinux.cfg"
{ printf 'set default=0\nset timeout=5\nmenuentry "Install Layer 8 (erases the disk)" {\n\tset background_color=black\n\tlinux /install.amd/vmlinuz %s\n\tinitrd /install.amd/initrd.gz\n}\n' "$ARGS"; cat /tmp/grub.cfg; } > "$ADD/boot/grub/grub.cfg"

xorriso -indev "$SRC" -outdev "$OUT" -map "$ADD" / -boot_image any replay -compliance no_emul_toc >/dev/null 2>&1
ls -la "$OUT"
