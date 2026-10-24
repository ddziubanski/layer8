# Contributing

Thanks for helping. Layer 8 is built in small, working slices, and contributions work best the same way.

## Before you start
- Read [SPEC.md](SPEC.md): it lists what's built, what's decided and what's open. [CLAUDE.md](CLAUDE.md) has the conventions.
- For anything bigger than a fix, open an issue first and say what you plan to build.

## Setting up
[backend/README.md](backend/README.md) covers the dev database (Docker Compose), running the API and worker, the web UI, and the optional dev Active Directory.

## Pull requests
- **API first:** everything the UI does goes through the API.
- **Settings:** every stored setting is editable under Settings and importable from an answers file. Secrets are files, never shown back.
- **Tickets:** every action taken from a ticket is logged on it.
- **Tests:** add or update them with your change. Backend: `cd backend && .venv/bin/pytest -q`. Frontend: `cd frontend && npm run typecheck && npx vitest run`.
- **Schema changes:** `alembic revision --autogenerate`, then `scripts/fix_enum_checks.py` on the new file.
- **No real data:** examples, tests, demo data and screenshots use example.com and corp.example only. A test fails if a real organisation's name appears. Never commit database backups, exports or customer data.
- **Licences:** only add dependencies or copied material under licences compatible with the AGPL-3.0 (MIT, BSD, ISC, Apache-2.0, MPL-2.0, LGPL/GPL-3.0 and the like; not GPL-2.0-only or proprietary terms). New source files start with `SPDX-License-Identifier: AGPL-3.0-or-later` (a test checks).

By contributing, you agree that your contribution is licensed under the [GNU Affero General Public License, version 3 or later](LICENSE).
