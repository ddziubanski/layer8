#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Development only: the app on http://localhost:8000 with the dev database and the
# Samba test AD (docker compose --profile ad up -d ad). Test credentials only (dev/answers.json).
cd "$(dirname "$0")/../backend" || exit 1
export HELPDESK_COOKIE_SECURE=false
export HELPDESK_RULES_DATABASE_URL=postgresql+psycopg://helpdesk_rules:helpdesk_rules@localhost:5432/helpdesk
# Test-domain settings, as the installer would set them (overwrites edits made in Settings).
.venv/bin/python -m app.cli settings import ../dev/answers.json
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
