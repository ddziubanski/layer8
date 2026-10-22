# Script execution review

Delegated script authors cannot retain technician execution permission after changing
code, parameters, interpreter, or restoring a version. Administrators review the current
version through the existing script editor before explicitly re-enabling technician use.
Metadata-only edits do not require another review.

Each version records its interpreter and first administrator review (identity and time),
available through the version-detail API. These are historical provenance, not a permanent
execution grant: current script policy and per-run approval remain authoritative. Later
policy changes do not replace the first-review record. Admin-authored code is reviewed by
that admin at save time; high-risk runs still require another active administrator, who
must be neither the requester nor the code author.

Executable changes, restores, deactivation, and stricter execution policy cancel unclaimed
work and clear sealed values. Claiming rechecks current version, requester activity and
execution rights, and any required approver's active admin status. Already-running work
cannot be recalled by the API; the runner's time limit still applies.

## Upgrade behavior

The version-interpreter migration uses each script's current interpreter for historical
versions because older interpreter changes were never recorded. Check older code before
restoring it. It does not invent review records for existing versions. All queued and
awaiting-approval runs are cancelled during this migration; request them again after
review. Running and completed runs are retained. Take the normal backup before upgrading.
