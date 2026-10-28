# Layer 8 setup guide

This guide is a practical, operator-focused setup walkthrough for Layer 8. It complements the existing `docs/install.md`, `backend/README.md`, and `docs/email-setup.md` documentation, and focuses on what you need to do to stand up, verify, and operate the service desk reliably.

## 1. What Layer 8 is

Layer 8 is a self-hosted IT service desk built for a Microsoft-heavy environment. It combines:

- Ticket intake via email and a self-service portal.
- Queue-based routing and assignment.
- Directory-aware person and device data.
- Script execution through pull-based runners.
- IPAM, knowledge base, change management, and asset context.
- Local account mode for standalone installs, with optional Active Directory / Microsoft 365 integrations.

The repo is intentionally opinionated about the deployment model:

- Production target: Linux with nginx, FastAPI, PostgreSQL.
- The app is OS-neutral; the installer and appliance package the OS.
- API and mailbox/worker run as separate services.
- Runners are pull-based and do not require inbound ports.
- Local install works without AD or Entra, but those integrations are optional add-ons.

## 2. Deployment options

Use the deployment path that matches your environment.

| Approach | Best for | Notes |
| --- | --- | --- |
| Appliance ISO | New production install | Fastest and most repeatable. Recommended for a fresh server. |
| Debian 13 installer | Existing Debian server | Good for migration or managed VM. |
| Local dev setup | Contributors and evaluation | Uses Docker Postgres and local Python environment. |

## 3. Requirements

### Minimum production sizing

- 2 vCPUs
- 4 GB RAM
- 40 GB disk for a small to mid-sized install
- One static DNS name such as `helpdesk.corp.example`
- TLS certificate for HTTPS (public CA or self-signed for early testing)
- PostgreSQL 15+ (or packaged version from Debian)
- nginx
- Python 3.12+ environment for the app

### Network and security

- Port 443 exposed to users and agents.
- Port 80 redirect to HTTPS only.
- No inbound ports needed on script runners.
- The app uses local filesystem storage for attachments and secrets.
- Secrets should be stored in a protected directory, not in the database.
- Use a dedicated `helpdesk` service account and a restricted `helpdesk_rules` DB user for SQL rules.

## 4. Production install path (recommended)

### 4.1 Appliance ISO install

1. Download the current ISO from the latest release.
2. Verify the SHA256 checksums.
3. Create a VM with:
   - 2 vCPU
   - 4 GB RAM
   - 40 GB system disk
   - one NIC
4. Boot the ISO.
5. Select the installation mode:
   - Install Layer 8 (recommended)
   - Erase entire first disk
   - Offline demo
6. Complete the setup wizard with:
   - time zone
   - DNS hostname
   - OS admin credentials
   - HTTPS certificate and key
   - first admin account
   - ticket numbering and label format
   - optional Microsoft 365 / Entra integration
   - optional Active Directory connection
   - optional Windows sign-in keytab
   - optional backup target
7. Reboot and sign in as the first admin.

The installer creates the system service account, database, app layout, nginx config, and services automatically.

### 4.2 Existing Debian 13 install

If you already run Debian 13, the installer can deploy Layer 8 directly.

```bash
sudo ./install/wizard.sh
# or
sudo ./install/install.sh --answers my-answers.json
```

The installer does the following:

- installs required packages
- creates the `helpdesk` service user and needed directories
- configures PostgreSQL and database users
- runs migrations
- applies the first admin and settings
- creates nginx and systemd service definitions
- starts API and worker services

## 5. Configuration model

Layer 8 stores settings in the app database, with environment variables as a runtime override. This is important for operations.

Common env vars in `.env.example` include:

- `HELPDESK_DATABASE_URL`
- `HELPDESK_TEST_DATABASE_URL`
- `HELPDESK_STORAGE_DIR`
- `HELPDESK_RULES_DATABASE_URL`
- `HELPDESK_SECRETS_DIR`
- `HELPDESK_GRAPH_TENANT_ID`
- `HELPDESK_GRAPH_CLIENT_ID`
- `HELPDESK_GRAPH_CLIENT_SECRET`
- `HELPDESK_MAILBOX`
- `HELPDESK_LDAP_URL`
- `HELPDESK_LDAP_BIND_DN`
- `HELPDESK_LDAP_BIND_PASSWORD`
- `HELPDESK_LDAP_BASE_DN`

For local development, copy:

```bash
cp .env.example .env
```

Do not commit `.env` to Git.

## 6. Local developer setup

For development and evaluation, use the bundled Docker/Postgres flow.

```bash
docker compose up -d db
cd backend
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp ../.env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed
.venv/bin/python -m app.cli create-admin you@corp.local "Your Name"
.venv/bin/uvicorn app.main:app --reload
```

Then start the frontend:

```bash
cd ../frontend
npm install
npm run dev
```

Open:

- API docs: `http://localhost:8000/docs`
- Frontend: `http://localhost:5173`

## 7. First-run checklist after install

Once the app is reachable, complete the setup in order:

1. Sign in as the break-glass admin.
2. Review `Settings > General`.
3. Set the portal URL, ticket numbering, and label format.
4. Configure branding if needed.
5. Create or confirm queues.
6. Set SLA and desk-hour policies.
7. Add ownership and manager rules.
8. Configure email intake.
9. Configure AD/LDAP sync if used.
10. Add rules for ticket routing.
11. Validate the worker and mailbox jobs.
12. Test user login and ticket creation.
13. Set up backup and scheduled jobs.

## 8. Optional integrations to enable

### 8.1 Microsoft 365 / Exchange Online email

Email intake uses Microsoft Graph against the service desk mailbox. This is optional, but if enabled, the mailbox becomes the central intake path.

Required inputs:

- tenant ID
- client ID
- client secret
- mailbox address
- allowed domains

See `docs/email-setup.md` and `docs/microsoft-365.md` for the exact configuration flow.

### 8.2 Active Directory / LDAP

Layer 8 can run with local accounts only, or synced with AD/LDAP. This is the usual production path.

Use:

- a read-only bind account
- LDAPS endpoints
- a base DN
- a CA cert if your DC uses a private CA

Important: AD sync should be limited to the accounts and groups you actually need to map to roles, requesters, and staff.

### 8.3 Windows sign-in (portal SSO)

For silent sign-in to the portal from domain workstations, use Kerberos + the service keytab. This is optional and should be configured only when you have Windows domain integration in place.

### 8.4 Runners

Install runners for scripts and network jobs. Runners:

- poll HTTPS over 443
- authenticate with their own token
- pull tasks
- push output back to the API
- do not require inbound network access

This is the preferred pattern for PowerShell or network tasks.

## 9. Post-install health checks

Use the built-in status page and manual CLI checks.

### Service checks

```bash
sudo systemctl status helpdesk-api helpdesk-worker nginx postgresql
```

### Log checks

```bash
journalctl -u helpdesk-api -u helpdesk-worker -f
sudo tail -f /var/log/nginx/error.log
```

### Useful CLI checks

```bash
sudo -u helpdesk sh -c 'set -a; . /etc/helpdesk/helpdesk.env; cd /opt/helpdesk/app && /opt/helpdesk/venv/bin/python -m app.cli check email'
sudo -u helpdesk sh -c 'set -a; . /etc/helpdesk/helpdesk.env; cd /opt/helpdesk/app && /opt/helpdesk/venv/bin/python -m app.cli check directory'
```

### Browser verification

- open the portal URL and login page
- verify HTTPS works
- create a test ticket
- test assignment and routing
- verify email intake and reply threading if configured

## 10. Operational hardening checklist

Before going live, make sure the following is true:

- TLS certificate is valid and not expired
- backups are configured and tested
- `HELPDESK_SECRETS_DIR` is permissions-restricted
- the first admin account is known and not deleted
- at least one non-directory recovery account exists
- worker service is monitored
- mailbox integration has test data and alerting
- runner tokens are rotated and managed
- AD sync is scoped properly
- disk has room for attachments and logs

## 11. Backup and restore

Backups are an integral part of production operations. The app supports encrypted backup archives with database, attachments, and secrets.

Common commands:

```bash
python -m app.cli backup create
python -m app.cli backup list
python -m app.cli backup verify FILE
python -m app.cli backup restore FILE --yes
```

Store the passphrase separately from the backup media. If using network storage, mount it with a restricted account, and test restore periodically.

## 12. Upgrade process

For a packaged install:

1. Take a backup.
2. Review release notes.
3. Run the installer again or apply the new package.
4. Confirm database migration status.
5. Restart services.
6. Validate status and login.

Do not perform an upgrade without a verified backup and a maintenance window.

## 13. Troubleshooting quick guide

### API not reachable

- check nginx status
- check `helpdesk-api` service
- check PostgreSQL connectivity
- inspect logs
- verify the correct hostname and certificate

### Worker not processing jobs

- check whether the worker is still running
- verify the queue and service state in Settings > Status
- inspect the worker log
- ensure the mailbox credentials and AD credentials are not expired

### Email tickets not created

- verify Graph app permissions
- check mailbox polling settings
- validate accepted domains
- check service account permissions
- inspect mailbox logs and the app log

### AD sync not working

- verify LDAPS and CA certificate
- confirm account DN and credentials
- ensure the search base includes the correct OUs
- check the sync interval and service logs

### Script runner not checking in

- verify the runner token
- confirm the runner can reach the API over HTTPS 443
- check firewall rules and certificate trust
- inspect runner logs and API status page

## 14. Recommended operating model

For a real production installation, the target operation model is:

- one admin account kept as a break-glass local admin
- one or more technician accounts with mapped AD group roles
- queue-based ownership for routing responsibility
- scheduled backup and restore drill
- status + log review on a routine schedule
- no direct editing of secrets by operators
- all integration credentials managed centrally and kept out of ticket data

## 15. Security reminders

- never keep production secrets in the repo
- never expose the database directly to the internet
- keep runners behind normal internal network controls
- restrict the `helpdesk_rules` DB account to read-only data access
- treat the portal and API as trusted network boundaries only when hardened correctly
- do not copy personal or tenant credentials into tickets, notes, or logs

## 16. Related docs

- `docs/install.md`
- `docs/email-setup.md`
- `docs/windows-sign-in.md`
- `docs/entra-sign-in.md`
- `docs/runner.md`
- `docs/recovery-console.md`
- `backend/README.md`
- `SPEC.md`

This guide is intentionally practical. If your goal is to run Layer 8 as a business-critical service desk, start with the ISO install, test email intake and AD sync in staging, then move into production with backup verification and a monitored worker cycle.

