#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# First boot of the appliance (helpdesk-firstboot.service): the setup wizard, or, when the ISO was
# built with an answers file, the unattended install. Either way the install's output is also kept
# in /var/log/layer8-install.log, and a failed unattended install says so on the console.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ANSWERS=${LAYER8_ANSWERS:-/opt/helpdesk-setup/answers.json}
LOG=${LAYER8_INSTALL_LOG:-/var/log/layer8-install.log}
ISSUE_DIR=${LAYER8_ISSUE_DIR:-/etc/issue.d}
INSTALLED=${LAYER8_INSTALLED:-/var/lib/helpdesk/.installed}
if [ ! -f "$ANSWERS" ]; then
  "$HERE/wizard.sh"
  rc=$?
  if [ ! -f "$INSTALLED" ] && mkdir -p "$ISSUE_DIR" 2>/dev/null; then
    # Left the wizard, or an install stopped: say how to carry on above the sign-in prompt.
    why=$(grep -E '^(install: |Nothing saved: )' "$LOG" 2>/dev/null | tail -1)
    printf '\n*** Layer 8: setup did not finish ***\n%sRestart the server to start setup again, or sign in as sdadmin and run:\n  sudo %s/wizard.sh\n\n' \
      "${why:+$why
}" "$HERE" > "$ISSUE_DIR/layer8-install.issue"
  fi
  exit "$rc"
fi
( umask 077; : >> "$LOG" ) 2>/dev/null || true
rc_file=$(mktemp)
{ "$HERE/install.sh" --answers "$ANSWERS" 2>&1 && echo 0 > "$rc_file" || echo $? > "$rc_file"; } | tee -a "$LOG"
rc=$(cat "$rc_file"); rm -f "$rc_file"
if [ "$rc" != 0 ]; then
  why=$(grep -E '^(install: |Nothing saved: )' "$LOG" 2>/dev/null | tail -1)
  msg=$(printf '*** Layer 8: the install stopped before it finished ***\n%s\nThe full output is in %s. Fix the problem, then run again (sign in as sdadmin):\n  sudo %s/install.sh --answers %s\n' \
    "${why:-install.sh exited with status $rc}" "$LOG" "$HERE" "$ANSWERS")
  printf '\n%s\n\n' "$msg"
  # The console's sign-in prompt clears the screen; this keeps the reason above it on every boot
  # until an install succeeds (install.sh removes it).
  if mkdir -p "$ISSUE_DIR" 2>/dev/null; then
    printf '\n%s\n\n' "$msg" > "$ISSUE_DIR/layer8-install.issue"
  fi
fi
exit "$rc"
