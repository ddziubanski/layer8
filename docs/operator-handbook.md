# Layer 8 operator handbook

This handbook is the day-2 operations guide for Layer 8. It is meant to complement the installation and integration documentation in this repository and provide a practical operational model for administrators and technicians.

## 1. Purpose and scope

Layer 8 is a self-hosted IT service desk for ticket intake, queue routing, technician work, incidents, devices, and operational context. In production, it should be treated as a business-critical system with scheduled maintenance, backups, role-based access, and monitoring.

This document covers:

- responsibilities and roles
- normal operating model
- runbooks for common events
- maintenance procedures
- incident response
- backup and restore preparation
- service health checks

## 2. Operating model

### 2.1 Roles

The following roles are the operational baseline:

- Break-glass admin: local admin account kept outside directory-based administration. This is the emergency account.
- System administrator: manages OS, services, TLS, backups, upgrades, and infrastructure.
- Service desk admin: manages settings, queues, SLAs, routing, branding, integrations, and permissions.
- Technician: handles tickets, sections, escalations, and standard queue work.
- Requester / portal user: submits requests through the portal or email.

### 2.2 Operational principles

- Keep the app, worker, database, and runner services healthy as a single stack.
- Keep at least one local admin account that works even if AD or Entra is unavailable.
- Use queues and owners for workload ownership rather than ad hoc assignment.
- Treat the mailbox, directory, backups, and runner credentials as privileged operational assets.
- Never store secrets in tickets or notes.

## 3. Standard service responsibilities

### Admin responsibilities

Daily:

- confirm the app login page is reachable
- confirm the worker is processing jobs
- check the status page and any scheduled warnings
- verify the worker heartbeat and mailbox status
- ensure backup jobs ran successfully

Weekly:

- review the system log for recurring failures
- verify ticket queue health and unassigned tickets
- review runner status and checks in
- verify certificate validity and OS updates
- test backup integrity

Monthly:

- review access roles and permissions
- review queue ownership and managers
- confirm AD sync coverage and stale data
- review attachments and storage growth
- check all integration health pages

### Technician responsibilities

Daily:

- process open tickets assigned to them or their queue
- confirm SLA states and escalation factors
- review priority and queue routing accuracy
- check for ticket updates from email or portal intake

Weekly:

- verify queue cleanliness and stale tickets
- review request statuses and unresolved blockers
- update requester communication where needed
- ensure notes and attachments are complete

## 4. Standard service checks

### 4.1 Daily health check

Perform this every working day.

1. Confirm internet-facing access to the portal and login page works over HTTPS.
2. Check status page for:
   - database availability
   - worker health
   - mailbox job health
   - directory sync health
   - disk usage warnings
3. Review active incident queue, unsupported states, and unassigned tickets.
4. Confirm the latest backup completed successfully.
5. Check for failed jobs or repeated retries.

### 4.2 Weekly health check

1. Review log entries for errors or patterns.
2. Review disk usage and attachment folder growth.
3. Review worker status and mailbox polling cadence.
4. Validate runner health and out-of-date runners.
5. Check queue ownership coverage, escalations, and unhandled ticket volume.
6. Review any disabled or stale integrations.

## 5. Runbooks

### Runbook A: service is unreachable

Symptoms:

- login page cannot be reached
- API requests fail
- browser shows TLS or timeout errors

Steps:

1. Check if nginx is running.
2. Check whether PostgreSQL is healthy.
3. Check whether the API service is running.
4. Check whether the worker service is running.
5. Review recent logs.
6. Confirm certificate and DNS are correct.
7. If the service was recently updated, review the app status and migration results.
8. If the issue is operationally severe, switch to maintenance mode if the app supports it; otherwise, communicate to stakeholders and proceed with system recovery.

Useful commands:

```bash
sudo systemctl status nginx helpdesk-api helpdesk-worker postgresql
journalctl -u helpdesk-api -u helpdesk-worker -n 200 --no-pager
sudo tail -f /var/log/nginx/error.log
```

### Runbook B: worker jobs are stalled or failing

Symptoms:

- emails not creating tickets
- no scheduled rules firing
- backups not running
- queue alerts not sending

Steps:

1. Check service status.
2. Review worker logs for repeated exceptions.
3. Check recent DB connectivity errors.
4. Confirm mailbox credentials remain valid.
5. Confirm AD integration is still reachable if used.
6. If the worker is stuck, use the supported restart flow from the app or OS layer.
7. Re-run a small validation once the worker recovers.

Useful commands:

```bash
systemctl status helpdesk-worker
journalctl -u helpdesk-worker -n 200 --no-pager
```

### Runbook C: email intake is failing

Symptoms:

- new tickets are not created from the mailbox
- replies are not threading to the correct ticket
- mailbox errors show in status page

Steps:

1. Validate the Graph or mailbox configuration in Settings > Email.
2. Check the mail account still has permission to access the mailbox.
3. Confirm the service account or app registration is valid.
4. Confirm mailbox polling is enabled.
5. Check if accepted domains are configured correctly.
6. Confirm the app can read the mailbox and that no rate-limit or permission warning is present.
7. Re-test with a known message or a test mailbox.

### Runbook D: AD/LDAP sync problems

Symptoms:

- users missing from directory sync
- no roles mapping correctly
- technicians cannot sign in
- ticket requesters not linked to correct people records

Steps:

1. Confirm the LDAP bind account is valid.
2. Validate the base DN and server certificates.
3. Check whether the AD domain is accessible from the app server.
4. Validate the service account has the minimum required read permissions.
5. Review sync logs and failed user records.
6. Check if AD group mapping changed or if the production environment is using stale group names.
7. If the issue is severe, temporarily fall back to local-account authentication if permitted.

### Runbook E: runner is offline or not checking in

Symptoms:

- no runner heartbeat
- script jobs stuck in queued or waiting state
- remote actions fail or do not run

Steps:

1. Verify the runner is running in the target environment.
2. Confirm network reachability to the app over HTTPS.
3. Confirm the runner token is valid and unchanged.
4. Check current time skew and certificate trust.
5. Review runner logs for connection or permission problems.
6. Confirm that the runner is allowed to reach the target systems it needs.
7. Re-register or reinstall the runner if necessary.

### Runbook F: backup failure or restore concern

Symptoms:

- backup job failed
- storage unavailable
- restore plan has not been tested recently

Steps:

1. Verify the backup destination is mounted and writable.
2. Check the backup passphrase is available and correct.
3. Check that the backup job has adequate permissions.
4. Review system log and the app log for backup errors.
5. Run a manual backup verification.
6. If restore is urgent, confirm the restore process was tested before and update the plan.

### Runbook G: certificate or hostname issue

Symptoms:

- browser warns on certificate trust
- login page shows mixed or invalid cert state
- app is reachable only by IP

Steps:

1. Confirm the hostname matches the certificate.
2. Confirm the certificate is unexpired.
3. Confirm the certificate chain and intermediate certificates are installed correctly.
4. Update nginx or app TLS configuration.
5. Reload the service and verify the page loads without warnings.
6. Re-check DNS records and local host resolution if needed.

## 6. Maintenance procedures

### 6.1 Routine patching

Schedule patching during low-usage periods.

1. Take a backup before the change.
2. Review release notes and migration requirements.
3. Patch the OS and dependencies in maintenance windows.
4. Restart the affected services.
5. Confirm status page health.
6. Validate login, ticket creation, and one representative workflow.

### 6.2 Storage and attachment maintenance

- monitor disk usage for attachments and database volume
- review stale tickets and attachments that no longer need retention
- ensure backup storage has enough headroom
- verify logs are not filling the disk

### 6.3 Database maintenance

- ensure PostgreSQL is regularly backed up
- monitor table growth and operational load
- monitor the app status page for database warnings
- review long-running queries if performance degrades

### 6.4 Service restarts

Use the supported service workflow and avoid ad hoc restarts during active incidents unless required.

```bash
sudo systemctl restart helpdesk-api
sudo systemctl restart helpdesk-worker
sudo systemctl restart nginx
```

### 6.5 Secret and credential rotation

Rotate the following periodically:

- mailbox app credentials
- AD bind account password
- runner authentication tokens
- backup credentials
- any Entra or Graph integration secrets

After rotation:

- update app settings
- re-test the integration
- confirm the worker still runs
- verify the status page reflects healthy state

## 7. Backup and restore readiness

### 7.1 Backup standard

A production install should have:

- automated scheduled backup jobs
- encrypted archive format
- passphrase stored separately from the backup
- tested restore procedure
- retention policy that matches your risk tolerance

### 7.2 Backup verification procedure

At least weekly:

```bash
python -m app.cli backup list
python -m app.cli backup verify <backup-file>
```

### 7.3 Restore drill

At least quarterly, perform a non-production restore test.

1. Identify a test environment or disposable VM.
2. Restore the backup to a clean install.
3. Validate access to the app.
4. Confirm ticket data is present.
5. Validate queue configuration and attachments.
6. Document the restore result and any gaps.

## 8. Incident response model

### Severity levels

- Sev 1: total outage or serious risk to production service
- Sev 2: major degradation or broken critical workflow
- Sev 3: minor issue affecting a limited subset of features

### Incident flow

1. Acknowledge the issue and identify the owner.
2. Capture symptoms and the time first seen.
3. Check status page and logs.
4. Identify whether the issue affects mailbox, directory, API, database, worker, or runner.
5. Apply the appropriate runbook.
6. Communicate status updates to stakeholders.
7. Restore service and validate with a focused business workflow.
8. Record the ticket, root cause, and remediation.

## 9. Maintenance windows

Recommended scheduling:

- patching: off-hours or low-volume window
- infrastructure maintenance: outside peak ticket flow periods
- data recovery drill: monthly or quarterly
- certificate review: monthly
- backup verification: daily or weekly

## 10. Escalation path

Escalate to the appropriate owner depending on the failing component:

- OS/network issue: system administrator
- app status issue: service desk admin
- mailbox or Graph issue: service desk admin / Microsoft identity owner
- AD issue: directory owner
- runner issue: automation owner
- security issue: system admin + security stakeholder

## 11. Useful command summary

```bash
# Application status
sudo systemctl status helpdesk-api helpdesk-worker nginx postgresql

# Logs
journalctl -u helpdesk-api -u helpdesk-worker -n 200 --no-pager
sudo tail -f /var/log/nginx/error.log

# Email checks
python -m app.cli check email

# Directory checks
python -m app.cli check directory

# Backup commands
python -m app.cli backup create
python -m app.cli backup list
python -m app.cli backup verify FILE
python -m app.cli backup restore FILE --yes
```

## 12. Operational checklist before release

Before promoting a production change or new integration, confirm:

- backup exists
- config change is reviewed
- service can be rolled back if needed
- impact is understood
- support owner is assigned
- monitoring and alerts are in place
- post-change validation has been scheduled

## 13. Summary

The production model for Layer 8 is straightforward: keep the stack healthy, keep backups valid, keep identities and credentials managed, and treat the mailbox and directory integrations as critical dependencies. This is an operational system and should be maintained like one.

For configuration details, start with the installation guide and integration-specific documents in the repository. For actual operations, use the runbooks above as the working baseline for normal support and incident response.

