# Backend

FastAPI + PostgreSQL. Production target is Linux, set up by an installer (see `../CLAUDE.md`); the code itself is OS-neutral.

## Dev setup

```sh
docker compose up -d db                 # from the repo root: dev Postgres + test database
cd backend
python -m venv .venv
.venv/bin/pip install -e '.[dev]'         # needs the Kerberos headers: libkrb5-dev / krb5-devel / krb5
cp ../.env.example .env

.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed        # default queue + starter SLA policies
.venv/bin/python -m app.cli create-admin you@corp.local "Your Name"   # asks for a password
# (or skip it: on a fresh install with no admin, sign in as admin / admin and choose a password)
.venv/bin/uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs. Sign in with `POST /api/auth/login`; requests that change data through the session cookie must send `X-Helpdesk-CSRF: 1`. With `HELPDESK_DEV_AUTH=true` (development only), an `X-Dev-User: <email>` header also works, without a password.

## Agent UI

```sh
cd ../frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to the backend on :8000
npm test
npm run api-types    # regenerate src/api/schema.d.ts after changing the API
npm run build        # dist/ is then served by the backend at /
```

## Test Active Directory

```sh
docker compose --profile ad up -d ad      # Samba AD DC for CORP.LOCAL on ldaps://localhost:1636, KDC on :1088
HELPDESK_TEST_LDAP=1 .venv/bin/pytest tests/test_ldap_live.py tests/test_kerberos_live.py
../dev/serve.sh                           # app on :8000, set up from dev/answers.json (like an install)
```

Users `jpark`, `dcole`, `mosei`, `ralvarez`, `newhire` (password `Correct-horse-1`) and a disabled `leaver`; groups
`SG-Helpdesk-Admins` (contains `SG-IT-Leads`, which contains `mosei`), `SG-Helpdesk-Agents` (`jpark`, `dcole`),
`SG-Network-Team` (`dcole`). Bind as `CN=svc-helpdesk,CN=Users,DC=corp,DC=local` / `Svc-readonly-pass-1`, CA in
`dev/ad/out/ca.pem`. In production, narrow `HELPDESK_LDAP_USER_FILTER` (or the base DN) to people, so service
accounts aren't synced as requesters.

Portal single sign-on: the container gives `svc-portal` the SPN `HTTP/helpdesk.corp.local` (AES keys only) and
exports its keytab to `dev/ad/out/http.keytab`. Upload that in Settings > Windows sign-in and turn it on.
`POST /api/auth/sso` with `Authorization: Negotiate <token>` signs the user in to the portal only (`portal_only: true`);
the console needs a password. `tests/test_kerberos_live.py` shows how to get a token from the test KDC.

## Settings

Admins change settings in the app (Settings), which stores them in the database (secrets as files in `HELPDESK_SECRETS_DIR`). A `HELPDESK_*` environment variable overrides a stored value and locks it in the UI. Installers and scripts use the CLI:

```sh
.venv/bin/python -m app.cli settings show
.venv/bin/python -m app.cli settings import answers.json   # {"general": {"portal_url": "https://helpdesk.corp.example"},
                                                           #  "windows_sign_in": {"keytab_file": "/root/helpdesk.keytab", "enabled": true}}
```

## Background worker

`.venv/bin/python -m app.worker` runs the jobs: email (read the intake mailbox, send queued email), directory sync, scheduled ticket rules and housekeeping. Admins watch them on Settings > Status, run one now, pause it, or restart the worker. `--once` runs every job once and exits. In dev: `../dev/worker.sh`.

A restart from the app makes the worker finish its current job and exit with status 75, so run it under a service manager that starts it again. With systemd (the installer's job):

```ini
[Service]
ExecStart=/opt/helpdesk/backend/.venv/bin/python -m app.worker
Restart=always
```

For a worker that's stuck, Settings > Status can also offer **Force restart**, which runs `HELPDESK_WORKER_RESTART_COMMAND` (server-only, never set in the app), e.g. `sudo -n systemctl restart helpdesk-worker` with a sudoers rule allowing just that command for the API's service account.

Setup for Exchange Online: `../docs/email-setup.md`.

## Backups

Settings > Backup sets the folder (normally a network share the installer mounts), the daily time, how many to keep and the passphrase; the worker's **Backup** job does the rest. Each backup is one file, `helpdesk-backup-YYYYMMDD-HHMMSS.hdbackup`: a gzipped tar of the database (`pg_dump -Fc`), the storage folder and the secrets folder, encrypted with AES-256-GCM in 1 MB chunks under a key derived from the passphrase (scrypt). Every backup is read back and checked against its manifest before it gets its final name. The server needs `pg_dump`/`pg_restore` (PostgreSQL client, same or newer major version as the server).

```sh
python -m app.cli backup create                       # now, into the backup folder (then prunes)
python -m app.cli backup create --to /tmp/x --passphrase-file pass.txt
python -m app.cli backup list
python -m app.cli backup verify FILE                  # decrypt and check every byte
python -m app.cli backup restore FILE                 # services stopped; asks first (--yes to skip)
```

Restore checks the whole file first, refuses a backup from a newer schema, restores the database in one transaction (`pg_restore --clean`), swaps in the storage and secrets folders (the old ones are kept as `<folder>.before-restore-<time>`), then runs the migrations. Keep the passphrase somewhere other than the server: a backup can't be opened without it.

To move to a new server or make a test copy across versions, use an export instead: `python -m app.cli export move.hdexport`, then on the new install `python -m app.cli import move.hdexport --dry-run` and again without `--dry-run`. An export carries configuration and data but no secret files (enter the directory password, Graph client secret, keytab and backup passphrase again), loads into an install that has no tickets yet, and takes only the columns both versions have. Email still queued on the old install is marked not sent.

## Tests

```sh
.venv/bin/pytest
```

Tests run against the `helpdesk_test` database and wipe it. They build the schema through the migrations and fail if a model changed without one.

## Changing the schema

```sh
.venv/bin/alembic revision --autogenerate -m "what changed"
```

Review the generated file. If it adds enum columns, run `.venv/bin/python scripts/fix_enum_checks.py <file>`: autogenerate emits each CHECK constraint twice with a clashing name.

### Midnight appearance

Select **Settings > Branding > Colours > Appearance > Midnight command center**, review the live preview, then Save. This optional preset uses dark navy panels, cyan actions with dark readable text, brighter status badges and compact ticket rows. It applies to the console, portal and sign-in screen. Choose **Custom colours** to restore the saved main/sidebar colours and dark-mode preference; those values are never replaced by the preset. Existing logos and backgrounds are retained.

Installer/CLI answers can set `{"theme": {"theme_preset": "midnight"}}` through `python -m app.cli settings import answers.json`. The default remains `custom`. No database migration is needed.
