# Normal operations checklist

This checklist is the baseline for day-to-day Layer 8 operations. It is intended for both administrators and technicians and can be used as a recurring operational worksheet.

## A. Admin checklist

### Daily

- [ ] Confirm the login page is reachable over HTTPS.
- [ ] Confirm the API service is healthy.
- [ ] Confirm the worker service is healthy.
- [ ] Check the status page for database, mailbox, worker, directory, and disk warnings.
- [ ] Confirm the latest backup completed successfully.
- [ ] Check for unassigned or stale tickets and queue buildup.
- [ ] Verify no obvious integration failures are active.

### Weekly

- [ ] Review system logs for recurring issues.
- [ ] Review queue ownership and manager coverage.
- [ ] Check runner status and run health checks.
- [ ] Verify backup integrity and storage availability.
- [ ] Review disk usage and storage growth.
- [ ] Confirm AD sync is healthy and mapped roles are still correct.
- [ ] Validate certificate expiry and any HTTPS warnings.

### Monthly

- [ ] Review permissions, admins, and queue ownership.
- [ ] Review backup retention and restore test status.
- [ ] Confirm external integrations still have valid credentials.
- [ ] Review ticket routing quality and a sample of assigned tickets.
- [ ] Review change log and configuration history for operational drift.
- [ ] Validate a representative end-to-end workflow: login, ticket intake, assignment, response, and closure.

### Quarterly

- [ ] Run a non-production restore drill.
- [ ] Review security settings and private credentials handling.
- [ ] Review service sizing and storage capacity.
- [ ] Review the environment for stale devices, scripts, users, and integrations.
- [ ] Validate maintenance documentation with current system state.

## B. Technician checklist

### Daily

- [ ] Process assigned tickets and verify SLA state.
- [ ] Check for tickets waiting acceptance or escalation.
- [ ] Review priority and queue alignment for new intake.
- [ ] Confirm requesters received a timely update where needed.
- [ ] Ensure notes, attachments, and status changes are complete.
- [ ] Resolve or escalate blocked tickets.

### Weekly

- [ ] Review tickets in your queues that are stale or over due.
- [ ] Confirm a sample of closed tickets has complete resolution notes.
- [ ] Check for duplicate or merged tickets that require cleanup.
- [ ] Review any tickets with missing attachments or incomplete requester follow-up.
- [ ] Ensure escalations and ownership changes are documented appropriately.

### Monthly

- [ ] Review personal queue health and unassigned workload.
- [ ] Check whether recurring issue categories need route or SLA adjustments.
- [ ] Review knowledge base article quality if you author content.
- [ ] Review changes to ticket templates, canned replies, or routing rules relevant to your queue.

## C. Operational sign-off for changes

Before making production changes, confirm all of the following:

- [ ] A backup exists or is scheduled.
- [ ] The change owner is identified.
- [ ] A rollback plan is documented.
- [ ] The expected impact is understood.
- [ ] Health checks are defined after the change.
- [ ] Affected staff are notified if there is any user-facing impact.

## D. Standard validation after a change

After any service or config change, validate:

- [ ] login works
- [ ] API is healthy
- [ ] worker service is healthy
- [ ] a representative ticket saves cleanly
- [ ] email or portal creation still works if enabled
- [ ] the status page shows no critical warnings
- [ ] the relevant integration still passes a basic test

## E. Quick response checklist for common issues

### If the app is slow or not responsive

- [ ] Check service health.
- [ ] Check database health.
- [ ] Check for spikes in queue or background jobs.
- [ ] Check disk usage.
- [ ] Check recent app or worker errors.

### If a ticket workflow is failing

- [ ] Confirm ticket creation still works.
- [ ] Confirm queue and routing rules are active.
- [ ] Check user permissions and ownership.
- [ ] Check notification or email workflow status.

### If a mail intake issue is reported

- [ ] Confirm mailbox connectivity.
- [ ] Confirm the app can access the mailbox.
- [ ] Check accepted domains and routing.
- [ ] Confirm the worker is still polling.

### If an integration is down

- [ ] Confirm the integration is enabled.
- [ ] Check credentials and permissions.
- [ ] Check the last successful run and the last failure.
- [ ] Validate the service account or app registration.

## F. Summary

This checklist is meant to be lightweight but consistent. The goal is to reduce surprise incidents and keep the service desk stable, predictable, and positively usable for both technicians and requesters.

