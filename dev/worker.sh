#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Development only: the background worker (email, directory sync, scheduled rules) against
# the dev database, with settings from the app (run dev/serve.sh first; it imports them).
# Like systemd's Restart=, it starts the worker again when it exits to restart (status 75),
# e.g. after Restart worker on Settings > Status.
cd "$(dirname "$0")/../backend" || exit 1
export HELPDESK_RULES_DATABASE_URL=postgresql+psycopg://helpdesk_rules:helpdesk_rules@localhost:5432/helpdesk
while :; do
  .venv/bin/python -m app.worker
  code=$?
  [ "$code" -eq 75 ] || exit "$code"
  echo "worker: restarting"
done
