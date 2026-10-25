# KACE ticket migration

**Settings > System > Imports** provides an admin-only KACE bundle preview and a separately confirmed Apply action. The web application does not contact KACE or accept KACE credentials.

**Validation:** built from the KACE SMA 15.1 API reference and synthetic fixtures. Live appliance interoperability remains unverified. Start with a small trial containing public/private comments, attachments, dates and work records. The reference incompletely describes some response fields, particularly comment text, dates and work duration.

## Export

Run the standard-library Python 3.11+ tool on a machine that can reach KACE:

```sh
python tools/kace-export/kace-export.py \
  --url https://kace.corp.example --organization Default --user migration \
  --timezone America/Los_Angeles --limit 10 --offset 0 --out trial.zip
```

Use a KACE API-enabled read-only administrator for the organization. Standard users may see only their own tickets. Passwords are prompted locally. Add `--mfa` for a current MFA code and `--ca-file /path/to/trusted-ca.pem` for a private CA. HTTPS verification remains enabled; redirects are refused. Authentication/MFA are the only POST requests; exporting changes no KACE data. All requests send `x-kace-api-version: 1` and use session cookies.

`--timezone` supplies the appliance timezone for dates without offsets. Verify interpretation during the pilot, including historical daylight-saving transitions. Unknown date formats stop export. Windows Python may need the `tzdata` package for IANA timezones. Source fields not present in the response cannot be reconstructed: preview flags missing ticket creation dates; missing note dates fall back to the ticket date. Work without an explicit minutes value remains an internal note, with a warning and no invented time entry.

The exporter fetches paged tickets, changes/authors, attachments and work. It emits only allowlisted person fields; no passwords, role grants, tokens or cookies. Resolutions become internal notes. Missing comment visibility defaults to internal. Keep the ZIP private: it contains ticket data and files. Existing output files are not overwritten, and failed exports leave no partial bundle.

## Preview and apply

1. Create an appropriate target queue under Settings > Queues. Set its access before importing. All tickets in a bundle go there; the original KACE queue stays in imported details. Use separate batches/queues for separate teams.
2. Open Settings > System > Imports, select that queue and a matching KACE ZIP, then **Preview import**. The database transaction rolls back and no attachment files are saved.
3. Review counts/warnings and take a current backup. Check the explicit confirmation, then **Apply reviewed import**. The API verifies the file/queue fingerprint. Changing either input clears the review.
4. Inspect imported tickets before larger batches. Keep the page open during the synchronous operation. Apply requests are not automatically retried.

Limits: **1,000 tickets**, **64 MiB upload**, **256 MiB expanded**, **25 MiB per archive entry**, **20,000 related records**. Export larger histories in batches with `--limit`/`--offset`. Offset paging is not a transactional snapshot: keep source activity quiet for cut-over, and verify totals.

## Preservation and repeat imports

- Title/body, mapped status/priority, category, requester/owner, available creation/closure dates, original searchable KACE number, source organization/queue and custom fields. Custom fields remain imported details, not new global configuration.
- Comments/authors and their attachments. Private notes never become public on reimport. Work and resolution notes stay internal. Checksums verify attachment bytes; filenames never determine storage paths. Imported files follow the existing migration policy (not malware-scanned).
- Existing accounts match by email/alias. Other historical accounts are inactive with no password. Existing accounts are never promoted/reactivated; an owner matched to a requester remains unassigned with a warning.
- IDs are namespaced by appliance and organization. Reimport updates imported tickets/comments/work without duplicating files or time records; new Layer 8 comments remain untouched. Previously imported fields can be overwritten and tickets moved to the selected queue, so this is for migration/cut-over rather than ongoing two-way sync. Use a consistent appliance origin across exports.

No notifications, routing rules, surveys or new SLA deadlines are generated. The importer uses direct migration records, not normal ticket creation. A failed transaction rolls back and removes its saved files. Missing prior imported records or changed bytes under an existing source attachment ID require review. A database advisory lock serializes KACE imports. Backup/restore is the recovery path after a completed import.

## API and archive

Admin-only multipart `POST /api/imports/kace/preview` takes `file` and `queue_id`. `POST /api/imports/kace/apply` also takes the preview `fingerprint` and `confirm=true`. The manifest's source URL is metadata, never a server fetch.

ZIP entries: `manifest.json` (`format: layer8-kace-export-v1`, HTTPS appliance origin, organization, ticket count), `tickets.jsonl` using the normalized models in `app/kace.py`, and attachment bytes at `files/<ticket>/<change>/<attachment>`. IDs are strings, supplied dates need offsets, and file metadata includes SHA-256. Duplicate/unexpected paths, missing files, checksum failures, encrypted entries, malformed values and excessive sizes/counts are rejected. An arbitrary ZIP/CSV or Track-It! bundle is not a KACE export bundle.

Existing Track-It! and Spiceworks CLI workflows remain unchanged.
