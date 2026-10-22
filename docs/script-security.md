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

## Privileged runners

In Settings > Runners, enable **Dedicated integration runner** for a host with AD write,
DHCP/DNS, SNMP or other privileged credentials. It accepts only built-in jobs explicitly
assigned to that runner. General scripts, even admin-authored ones, and unpinned integration
jobs cannot land there. Explicitly select the runner in the relevant integration settings;
an empty "any runner" selection will not use a dedicated host. Keep general scripts on a
separate service account and machine with only the rights those scripts need.

For AD, set **Settings > Directory > Runner for AD account changes**. This pins group/OU
changes, new accounts, deferred account enabling and offboarding jobs to that host. The
same setting is importable as `directory.ad_runner_name` in installer answers. A missing,
disabled or non-PowerShell selected runner causes an error; there is no silent fallback.

Existing runners retain general mode for compatibility. Enabling this setting prevents
future claims; it cannot stop work already running. Removing a runner cancels pending jobs
pinned to it and clears their secrets, rather than letting another host inherit them.
This is a dispatch boundary, not an OS sandbox: a compromised runner token or administrator
remains privileged within its configured role. Restrict OS credentials and protect tokens.
