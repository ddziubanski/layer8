#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Development only: run the backend tests on Debian 13's own Python and PostgreSQL, the
# versions the appliance ships, in a throwaway container:
#   docker run --rm -v "$PWD":/src:ro debian:trixie sh /src/dev/debian/check.sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-venv python3-dev gcc libkrb5-dev krb5-user \
  postgresql postgresql-client ca-certificates >/dev/null
echo "== $(python3 --version), $(psql --version)"
pg_ctlcluster "$(ls /etc/postgresql)" main start
su postgres -c "psql -q" <<'SQL'
CREATE ROLE helpdesk LOGIN SUPERUSER PASSWORD 'helpdesk';
CREATE DATABASE helpdesk OWNER helpdesk;
CREATE DATABASE helpdesk_test OWNER helpdesk;
SQL
su postgres -c "psql -q -d helpdesk_test" < /src/dev/initdb/02-rules-role.sql || true
cp -r /src/backend /app
cd /app
rm -rf .venv storage secrets
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e '.[dev]'
HELPDESK_TEST_DATABASE_URL=postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk_test .venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -5
