# GitHub appliance updates

Settings > Updates checks **published stable releases** of `ddziubanski/layer8`, compares numeric versions, and offers a reviewed update only when its publisher signature verifies. Page loads and status polls do not contact GitHub. Checks are explicit, cached for one minute, and off by default. No GitHub token is needed for the public repository. Main, draft PRs, prereleases and downgrades are never installed.

## One-time preparation

This is an application updater for the **Debian 13 amd64 appliance with Python 3.13**, not an OS/package-manager updater. Existing installations need the first updater-enabled application release installed manually once. From that reviewed release, `sudo sh install/install-updater.sh` provisions the root service and maintenance gate without changing application settings or credentials. New full installs provision those files automatically. The bootstrap checks Nginx before reloading it; an unfamiliar configuration requires a local administrator to install/review the gate manually.

The administrator must independently authenticate the publisher's **Ed25519 public key** and install its PEM public-key file under `/etc/helpdesk/update-keys/` as root:root, mode 0644 (directory root-owned, not group/other writable). Compare its fingerprint through a trusted channel. Never download and trust a key from the same unverified update being installed. No production key is invented, generated or provisioned by this PR. For rotation, authenticate and install the replacement key before releases switch signatures; at most 20 PEM keys are considered.

Set a backup passphrase under Settings > Backup and keep it off the appliance too. Enable GitHub release updates under Settings > Updates (or import `{"updates":{"updates_enabled":true}}` in an answers file). The service creates a separate local encrypted recovery backup under `/var/lib/layer8-updater/work/<request>/backup/`; this does not replace regular off-server backups. It requires at least 4.5 GiB free on the application disk, plus enough room for the encrypted backup on `/var/lib`.

Direct outbound HTTPS is needed to `api.github.com`, `github.com`, `release-assets.githubusercontent.com` and `objects.githubusercontent.com`. Connections verify TLS, have time/size limits, refuse other redirect destinations and do not use ambient proxy credentials. Configurable proxies/private repositories and local USB/share package import are future work. Existing releases containing only an ISO/source archive cannot be applied as updater packages.

## Administrator flow and progress

1. Check GitHub for updates, read the release notes and verified package size, then choose Review update.
2. Finish active work, choose a maintenance window and confirm the interruption. Back up and install submits the exact reviewed manifest digest to a fixed system service. It never supplies a shell command or arbitrary download URL.
3. Keep the Updates tab open. It shows nine stages, download bytes, elapsed time, an activity history and individual verification results. During API downtime, Nginx serves sanitized root-written progress at an unpredictable capability URL returned to the admin. There are no directory listings, secrets, site counts, private paths or raw process logs in that response, and capability URLs are excluded from proxy access/error logs. Treat the URL as temporary access to non-sensitive progress; don't post it publicly. Refreshing the whole browser page during maintenance may show the maintenance notice because the app server is stopped.
4. A successful update returns normal access only after every required check passes. The installed version and complete verification results remain visible under Updates. Previous application files and the encrypted backup are retained.

The nine stages are signature/review verification; download; stopped-service encrypted backup; offline dependencies; migrations; application checks; worker start; worker verification; completion. A rollback/recovery state replaces the normal sequence when needed; no estimated completion time is invented.

## What counts as success

- A root-owned, independently trusted Ed25519 key verifies the exact reviewed manifest; repository, stable version, platform, minimum starting version, package name, size and SHA-256 all match.
- Only bounded regular files/directories are extracted; links, traversal, duplicates and special files are refused. Application and build-source archive versions match the manifest. Offline wheels, frontend and licence/source files are present.
- Before any schema change, API and worker services are stopped behind Nginx maintenance and a full encrypted database/files/secrets backup is created and read back for verification.
- The installed Python distribution and imported application version both match the target, and the database is at the target's single Alembic head.
- Ticket, comment, attachment and user record counts do not decrease across migration. This checks for unintended bulk loss; it does not prove every field or integration is correct.
- Installed frontend/source files match hashes taken from the verified bundle. The API responds with the expected version and database connectivity. Its frontend index/entry assets and exact build-source download are fetched and verified over localhost.
- API and worker services are active. A newly started worker's version, start time, heartbeat and PID match the active systemd process, which must remain active without a PID change through a short stability check. This establishes worker readiness, not successful live calls to every optional integration; it does not send test email or invoke provider actions.

The CLI backup path now loads stored settings/secrets in its own process, so the updater uses the same backup passphrase as the API. The worker publishes an initial, versioned heartbeat before potentially long integration jobs.

## Failure and local recovery

Downloads, verification and extraction happen before downtime. A preflight failure leaves the current app running. Dependency/migration/application-check failures attempt recovery with the previous application; if migrations were attempted, the verified full backup is restored with that previous version before services restart. Updater rollback resets/restores the dedicated application public schema in one transaction, including removal of tables/foreign keys added by the failed migration; a failed SQL restore rolls that transaction back. Normal CLI restores retain their existing behavior. The failed update and its recovery state remain in the activity results.

Once the worker is starting it may already have sent mail or run integrations. A worker validation failure therefore stops services and leaves maintenance active for local review instead of restoring a snapshot over possible external actions. A failed restore, power loss, service timeout or reboot also retains maintenance and requires local recovery. The update service is not automatically retried at boot. Never remove maintenance merely to dismiss an error.

Log in as the OS administrator. Inspect `systemctl status layer8-update.service`, `journalctl -u layer8-update.service`, root-owned `/var/lib/layer8-updater/status.json`, and `/var/lib/layer8-updater/details.log` (root-only bounded process diagnostics; may contain site data). That status records the request, retained backup and previous application path. Confirm both application services are stopped before restoring anything. Use the retained previous application at `/opt/helpdesk-previous-<request>` with its matching virtual environment and the verified backup; see [install.md](install.md) for the full backup-restore procedure. Preserve the failed code/data for diagnosis. An administrator must verify the chosen code/schema pairing, database, frontend, source download and worker before clearing `/var/lib/layer8-updater/maintenance` and any interrupted inbox request. Application paths, recovery files and keys are not writable through the UI. Successful backups/previous releases are not automatically pruned; an OS administrator can archive them after acceptance while retaining off-server recovery copies.

## Publisher: build and sign a release

Build/test the offline appliance bundle with the normal Debian 13 ISO toolchain. Bump `app.VERSION` and the `helpdesk` distribution version together before packaging. The bundle must include matching wheels (including all dependencies), built frontend, source archive and LICENSE. The target must include the versioned-worker-heartbeat protocol. Then, using an independently held Ed25519 private key outside the repository:

```sh
python3 tools/build-update.py iso/build/bundle --out /secure/release-1.2.0 \
  --version 1.2.0 --minimum-version 1.1.0 --key /secure/publisher-ed25519.pem
```

Publish `layer8-v1.2.0-update.tar.gz`, `layer8-update.json` and the raw 64-byte `layer8-update.sig` together on **that version's stable GitHub release**. The manifest fixes the repository, platform, Python version, minimum version, artifact size/hash and file name. Signing gives the package permission to install code; protect the private key and test every release's successful upgrade, forced failure recovery and reboot/interruption behavior in a disposable appliance VM before publishing. Checksums or GitHub asset digests alone are not signatures. A changed manifest after review is refused; check/review again.

The root updater itself is deliberately outside application releases and is not overwritten by their payloads. Updating the trust boundary/systemd/Nginx/OS configuration requires a separately reviewed local bootstrap change. Tests cover protocol/security boundaries, simulated service/database failure recovery, real Nginx progress during API downtime, API/frontend flows, and a disposable offline Debian container using real PostgreSQL, encrypted backup/restore, source/package verification and API/worker processes. The container adapts only GitHub transport and systemd process management. They do not substitute for a full released-package appliance VM upgrade test.
