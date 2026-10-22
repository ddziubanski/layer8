#!/bin/sh
# Development only: install an appliance ISO into a throwaway QEMU/KVM VM and wait until the
# service desk answers on https://localhost:8443 (the VM's 443).
#
#   dev/iso-test.sh iso/build/layer8-<version>.iso [bios|uefi]
#
# Build it with iso/build.sh --demo (or --answers) so first boot installs without the wizard.
# The installer's serial console goes to iso/build/vm-<firmware>.log.
set -eu
ISO=$1 FW=${2:-bios}
DIR=$(cd "$(dirname "$0")/.." && pwd)/iso/build
DISK=$DIR/vm-$FW.qcow2
LOG=$DIR/vm-$FW.log
rm -f "$DISK" "$LOG"
qemu-img create -q -f qcow2 "$DISK" 40G
FIRMWARE=""
if [ "$FW" = uefi ]; then
  cp /usr/share/edk2/x64/OVMF_VARS.4m.fd "$DIR/vm-vars.fd"
  FIRMWARE="-drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd -drive if=pflash,format=raw,file=$DIR/vm-vars.fd"
fi
# shellcheck disable=SC2086
qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 $FIRMWARE \
  -drive file="$DISK",if=virtio,format=qcow2 -cdrom "$ISO" -boot once=d \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:8443-:443,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0 \
  -display none -serial file:"$LOG" -monitor unix:"$DIR/vm-$FW.mon",server,nowait -daemonize -pidfile "$DIR/vm.pid"
echo "VM started (pid $(cat "$DIR/vm.pid")); installing. Serial log: $LOG"
up() {  # wait until the app answers; $1: what we're waiting for
  start=$(date +%s)
  while :; do
    if curl -sk --max-time 5 https://localhost:8443/api/health | grep -q ok; then
      echo "$1: up after $(( $(date +%s) - start )) s"
      return 0
    fi
    [ $(( $(date +%s) - start )) -gt 2700 ] && { echo "$1: gave up after 45 minutes"; tail -30 "$LOG"; exit 1; }
    kill -0 "$(cat "$DIR/vm.pid")" 2>/dev/null || { echo "$1: the VM stopped"; tail -30 "$LOG"; exit 1; }
    sleep 10
  done
}
up "Install"
# The worst case: power cut right after setup. It must come back on its own.
python3 -c "
import socket, time
s = socket.socket(socket.AF_UNIX); s.connect('$DIR/vm-$FW.mon'); time.sleep(0.3); s.recv(4096); s.send(b'system_reset\\n'); time.sleep(1)"
sleep 20
up "After a hard reset"
curl -sk -o /dev/null -w "Demo sign-in: HTTP %{http_code}\n" -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.local","password":"Demo-helpdesk-2026"}' https://localhost:8443/api/auth/login
