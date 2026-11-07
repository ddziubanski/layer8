#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# First boot of the appliance (helpdesk-firstboot.service): the setup wizard, or, when the ISO was
# built with an answers file, the unattended install. Either way the install's output is also kept
# in /var/log/layer8-install.log, and a failed unattended install says so on the console.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ANSWERS=${LAYER8_ANSWERS:-/opt/helpdesk-setup/answers.json}
LOG=${LAYER8_INSTALL_LOG:-/var/log/layer8-install.log}
if [ ! -f "$ANSWERS" ]; then
  exec "$HERE/wizard.sh"
fi
( umask 077; : >> "$LOG" ) 2>/dev/null || true
rc_file=$(mktemp)
{ "$HERE/install.sh" --answers "$ANSWERS" 2>&1 && echo 0 > "$rc_file" || echo $? > "$rc_file"; } | tee -a "$LOG"
rc=$(cat "$rc_file"); rm -f "$rc_file"
if [ "$rc" != 0 ]; then
  why=$(grep '^install: ' "$LOG" 2>/dev/null | tail -1)
  printf '\n*** Layer 8: the install stopped before it finished ***\n%s\nThe full output is in %s. Fix the problem, then run again (sign in as sdadmin):\n  sudo %s/install.sh --answers %s\n\n' \
    "${why:-install.sh exited with status $rc}" "$LOG" "$HERE" "$ANSWERS"
fi
exit "$rc"
