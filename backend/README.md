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

### Meraki discovery (optional, read only)

In **Settings > Meraki**, save a Dashboard API key for a dedicated administrator with read-only access to the intended networks. Enter explicit network IDs (one per line, at most 20), test the selected networks, then enable discovery and choose **Read Meraki now**. The worker must be running; Settings > Status shows its result. API requests use only `GET https://api.meraki.com/api/v1/networks/{id}` and `/networks/{id}/clients`. Redirects are refused. Other Dashboard regional API hosts are not supported in this first slice.

Settings are importable through `python -m app.cli settings import answers.json` using section `meraki` with `meraki_enabled`, `meraki_api_key`, `meraki_network_ids`, and `meraki_sync_minutes`. The key is kept in a write-only secret file, never returned by the API. OAuth authorization is a follow-up; this slice does not accept OAuth access tokens or refresh tokens.

Observations cover the previous 24 hours and are cached separately from documented IPAM data. A failed or malformed response keeps the previous complete snapshot. Discovery is limited to 100 pages per network and 40,000 clients per snapshot; exceeded limits fail visibly rather than save a partial result. Reads retry HTTP 429 at most twice with waits capped at five seconds. A PostgreSQL transaction lock prevents concurrent snapshots.

Technicians can search **IP addresses > Meraki clients**. Document an address with its MAC to show context on that IPAM record and its device/ticket cards. Matching requires one selected-network observation and one documented record for that MAC; duplicate or overlapping-network identities are suppressed. Hostnames and IPs do not auto-link devices. The last reported switch/AP, switch port, VLAN and SSID are evidence from Meraki, not a live connectivity guarantee. Observations become stale after two sync intervals (at least 30 minutes), or when the last client sighting is missing/over a day old. Removed networks and a disabled integration are hidden immediately after settings refresh; successful discovery replaces the old snapshot. Turning discovery off retains the cache in the database for backup/export until the next successful snapshot.

Public API: `GET /api/meraki/status` and `POST /api/meraki/test` require an admin; `GET /api/meraki/clients?q=...&limit=200` requires a console technician (limit 1–1000). The latter lists cached observations and makes no external call. `POST /api/system/jobs/meraki/run` uses the existing admin worker control. No new ticket actions or outbound notifications are introduced.

Schema migration `a86d20f49c71` adds only `meraki_clients`. Reconcile its parent revision when rebasing after other pending migration PRs.

Contracts: [Get network](https://developer.cisco.com/meraki/api-v1/get-network/), [Get network clients](https://developer.cisco.com/meraki/api-v1/get-network-clients/), [OAuth follow-up reference](https://developer.cisco.com/meraki/api-v1/oauth-overview/).


### Optional Meraki OAuth connection

The Meraki settings also support OAuth instead of an API key. This is one
administrator-authorized connection for this Layer 8 installation, shared by the
API and discovery worker; it does not change technician sign-in or permissions.
API-key authentication remains the default and continues to work unchanged.

1. Use an HTTPS portal base address in Settings > General (for example,
   `https://helpdesk.corp.example`). OAuth currently supports the `meraki.com`
   region only.
2. Register a confidential integration at <https://integrate.cisco.com>. Register
   the exact redirect URI `https://helpdesk.corp.example/api/meraki/oauth/callback`.
   Grant only `dashboard:general:config:read` and
   `dashboard:general:telemetry:read`. These are the documented scopes for the
   network-details and client-list GET endpoints; no write scope is requested.
3. Before connecting, deploy the callback location from
   `install/files/nginx-helpdesk.conf`. It suppresses query-bearing access/error
   logs for this exact endpoint. Existing installations must apply that location
   and reload Nginx; custom proxies, WAFs and request tracing must likewise omit
   this callback's query parameters. The application redacts the query from
   Uvicorn logs before authentication and returns a no-store, no-referrer redirect.
4. In Settings > Meraki, choose OAuth, save the client ID and client secret, and
   enter the explicit network IDs to read. Click **Connect Meraki** and authorize
   with a Meraki organization administrator. Then test the selected networks and
   enable discovery. OAuth scopes authorize organization data; Layer 8's explicit
   network list limits which of that data its discovery reads.

All four settings (`meraki_auth_mode`, `meraki_client_id`,
`meraki_client_secret`, existing `meraki_network_ids`) are also available through
`PUT /api/settings/meraki` and the CLI settings answers-file import. The client
secret and token/pending-state files live under `HELPDESK_SECRETS_DIR` with mode
0600, never in the database or API responses. Do not copy these files between
installations. Treat backups containing them as credentials.

The admin API is `POST /api/meraki/oauth/connect` (returns the authorization URL),
`GET /api/meraki/oauth/callback` (provider callback), and
`DELETE /api/meraki/oauth/connection` (forgets locally and attempts provider
revocation). Cookie-authenticated mutations require the usual CSRF header. A
callback must match the initiating admin, browser session, configuration and
single-use state within ten minutes. Only the latest connection attempt is kept.
Changing credentials or the portal address requires reconnecting.

API processes and the worker serialize refresh/disconnect using a PostgreSQL
transaction advisory lock. A durable marker is saved before submitting a rotating
refresh token. A crash, timeout, rejected response or failed token write requires
reconnecting; the old refresh token is never retried automatically. A successful
rotation is persisted before any discovery read, including reads that later fail.
No Meraki network configuration is changed. Cached observation matching and
atomic snapshot replacement follow the existing discovery behavior.

Disconnect removes local tokens and pending authorization before attempting
remote revocation. If revocation cannot be verified (including an interrupted
refresh), the screen instructs the administrator to remove the integration in
Meraki Dashboard under Organization > Integrations. Meraki says revoked access
tokens can take up to ten minutes to stop working. Meraki does not notify this
application when an administrator revokes access remotely; failed reads retain
the previous observation snapshot.

Protocol reference: [Meraki OAuth overview](https://developer.cisco.com/meraki/api-v1/oauth-overview/).
Endpoint scopes were checked against Cisco's
[OpenAPI specification](https://github.com/meraki/openapi/blob/master/openapi/spec3.json).


### On-demand Meraki network diagnostics

Admins may enable **Allow read-only network diagnostics** in Settings > Meraki
(`meraki_diagnostics_enabled`, also accepted by the settings API and CLI answers
import). It defaults off. The existing discovery-only OAuth connection still
requests its original two scopes while this option is off. Enabling diagnostics
adds `switch:telemetry:read` and `wireless:telemetry:read`; update the confidential
application registration and reconnect. Changing this option invalidates the
OAuth configuration fingerprint, rather than silently assuming broader access.
API-key authentication remains available with a dedicated read-only administrator.

Technicians open IP addresses > Meraki clients, expand **Network diagnostics**,
then explicitly choose **Read diagnostics**. Expanding the panel reads only the
local cache. No browser polling calls Meraki, and no diagnostics run automatically
with discovery. Ticket/device/IPAM cards retain their existing observation-only
behavior; a future ticket diagnostic action must add ticket audit history.

For a wired client, Layer 8 checks the current client ID/MAC/attachment and the
switch's current network membership, then reads the matching last-known port's
status, enabled state, speed/duplex, client count, uplink status, errors and
warnings. Other ports and CDP/LLDP payloads are not returned to the technician.
The port may be shared, and the read does not prove this client is connected.

For a wireless client, the same identity/attachment check precedes reading the
last fifteen minutes of connectivity events. Only one descending page of at most
50 events is read; a visible truncation notice indicates additional pages. The
app never follows the next-page URL. Event timestamps, type/subtype, severity, AP
serial, band/channel, RSSI and duration are retained; arbitrary eventData, capture
IDs and provider links are omitted. RSSI is retained as the provider reports it;
this endpoint does not document units, so no dBm conversion or health inference is
made. An empty result makes no health claim.

The result distinguishes the client discovery timestamp, diagnostic attempt,
successful read, and wireless source event times. Port status has no source
timestamp in this endpoint; its read time is labelled accordingly. These are
separate cached observations, not edits to documented IPAM or discovery records.

API (agent/admin only):

- `GET /api/meraki/clients/{network_id}/{mac}/diagnostics`: local result or null;
  never contacts Meraki.
- `POST` on the same path: explicit read; cookie authentication requires the usual
  CSRF header. Repeated attempts within 60 seconds reuse the cached result.

A shared PostgreSQL advisory lock admits one diagnostic read at a time across API
processes. A different client is limited to one new attempt per five seconds;
busy work returns 409, and rate limits return 429 with Retry-After. Failed attempts
also consume the per-client cooldown. Provider failures return an `error` alongside
the previous successful `data` (or null), with the updated attempt time; they do
not erase earlier evidence. Removed networks, disabled connections/features and
changed attachments cannot expose an old result. Client identity or location
changes require discovery before reading from the new attachment.

Migration `b97e31a50d82` adds the separate `meraki_diagnostics` cache after
`a86d20f49c71`. Entries not attempted for 24 hours are discarded, and the cache is capped at
500 entries when new reads are made. Last successful evidence can be older after
repeated failures; its read timestamp is retained and labelled. No secrets or raw provider responses are stored in this cache.
No POST/PUT/DELETE is sent to Meraki's configuration API, and no cable test,
port-cycle or configuration action is included.

Contracts and scopes were checked against Cisco's official
[OpenAPI specification](https://github.com/meraki/openapi/blob/master/openapi/spec3.json),
[switch port status](https://developer.cisco.com/meraki/api-v1/get-device-switch-ports-statuses/),
and [wireless connectivity events](https://developer.cisco.com/meraki/api-v1/get-network-wireless-client-connectivity-events/).
