# Layer 8

Notes for anyone (people or Claude) working on this code.

The product is called **Layer 8**: a self-hosted IT service desk. Be playful with the name where it fits (the OSI "layer 8" is the user), and keep the UI itself plain and clear. Internal names stay as they are so existing installs keep working: the `helpdesk` service account and database, `/opt/helpdesk`, `HELPDESK_*` settings, the `app` package. It blends ideas from ManageEngine, Track-It!, KACE and Spiceworks. Read SPEC.md for requirements and open questions before building anything.

## Working style
- Prefer small, working vertical slices over big-bang scaffolding. Say what you plan to build before large changes.
- Commit in small, reviewable steps.

## Stack
- Backend: Python, FastAPI
- Database: PostgreSQL
- Frontend: React
- Built for a Windows / Active Directory environment; network examples assume Cisco Meraki switches (Dashboard API). The runner's Arista EOS script language is generic and stays, but don't design features around one switch vendor.

## Hard requirements
- Robust: ticket routing, assignment, attachments.
- Tickets can be created by email AND by a self-service portal. Both feed the same queue and the same routing engine.

## Decisions already made
- License: MIT (LICENSE at the repo root). Only add dependencies and copied material whose licenses are compatible; KACE community queries (no license) are inspiration only, never copied.
- Production target is a Linux installation. An installer detects the distro and decides which OS packages to install; the app itself stays OS-neutral. API and mailbox worker run as separate services. Paths come from config. Docker Compose is used for the dev database.
- Script execution goes through a separate runner component, so a runner can live on another host (e.g. a domain-joined Windows box for PowerShell over WinRM).
- Malware scanning is NOT built here. Exchange Online scans email before delivery; portal and agent uploads are not scanned (accepted risk).
- UI targets desktop browsers (agents work at desks); tablet should be usable, phones are not a design target.
- Must run standalone with local data: local accounts (Argon2 passwords, server-side sessions) need no AD/Entra. Directory sign-in, email intake and other integrations are optional add-ons.
- Permissions are primarily AD/LDAP groups: role mappings (group -> agent/admin, nested, highest wins) set directory users' roles; roles can't be edited by hand for them. Local accounts with a password are never taken over by sync (break-glass admin). Queue owners/managers can be users or groups; groups are local or directory (synced by objectGUID).
- Email intake and notifications use Microsoft Graph against Exchange Online (app registration scoped to the intake mailbox).
- Patching is NOT built here. A third party (e.g. Action1) does patching. The patch integration reads its data (compliance, pending reboots) and raises tickets or links changes. Separately, opt-in software deployment through Action1 uses dedicated credentials and an explicit organisation allowlist, technician review and the action1_deploy permission; it does not implement patching.
- Remote control is NOT built here. The ticket has a "Remote session" button that launches existing Windows tooling and logs the session to the ticket.
- The script library has two kinds of items: read-only queries (reports and dynamic groups) and scripts that act on devices.
- Devices come first from AD computer objects, synced with the directory sync (Action1 or our own agent can add data later).
- Runners pull: a runner polls the API over HTTPS 443 with its own token, claims jobs and posts output back. No inbound ports on runner hosts.
- Script versions live in the database (numbered, with author, time and diff; runs record the version used), not in a git repo.
- IPAM is a light module built into the suite (not a full IPAM like NetBox): subnets, VLANs and IP records, kept current by scans (runner-driven ping sweep and ARP, Windows DHCP and DNS, SNMP discovery), on demand and scheduled. Scans flag differences; they don't silently change documented data.

## Reference material
- GUI sample (queue, ticket detail, self-service portal): HTML in `gui-sample/`.
- Action1 (patch data, read-only): REST API docs https://app.action1.com/apidocs/. The PSAction1 module's terms of use don't allow copying from it: write from the API docs.
- Query inspiration: https://github.com/KACE-SMA-Tools/K1000-Database-Queries (MySQL for KACE, site-specific values, no license seen). Rewrite queries for Postgres; do not bulk-copy.

## Conventions
- API-first: everything the UI does must be available through the API.
- Every action taken from a ticket (script run, remote session, port bounce) is logged on that ticket.
- Never commit secrets, and never commit real organisations' data: examples, tests, demo data and screenshots use example.com and corp.example (reserved for documentation). `backend/tests/test_no_site_names.py` guards this, and `iso/build.sh` refuses to ship database files.
- Configuration lives in the app: every stored setting is editable in a Settings tab (and the API). Values go in the database (`app_settings`); secrets (passwords, client secrets, keytabs) are files in `HELPDESK_SECRETS_DIR` (mode 0600), written but never shown back. Only bootstrap values (database URLs, paths, dev switches) are environment-only. A `HELPDESK_*` environment variable still overrides a stored value and shows as read-only in Settings.
- Initial setup comes from an installable ISO that sets every option during install. Keep every setting importable non-interactively: `python -m app.cli settings import answers.json`.
