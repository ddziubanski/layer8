# Relay deployment kits

Administrators can download the Windows kit from `/windowskit` or Settings > Relays.
The separate Linux appliance kit is at `/appliancekit`. Downloads contain code and
checksums, never relay tokens. Checksums detect corruption; they are not publisher
signatures. Review scripts and apply your organization's signing policy before use.

## Windows Server prerequisites

Windows Server 2025 Server Core is the intended Windows relay target; Desktop
Experience is not required. Live Server Core and disposable-domain validation is
still pending. Use Windows PowerShell 5.1 in an elevated session when applying
Windows features. The relay uses a scheduled task, outbound HTTPS, and Python
3.10 or newer installed for all users. A basic relay needs no additional server
roles. Python is installed separately from a trusted, approved installer.

Preview the prerequisite check after extracting the kit:

```powershell
.\Install-RelayPrerequisites.ps1
# Select only the tools required by your relay jobs:
.\Install-RelayPrerequisites.ps1 -Tools ActiveDirectory,Dns,Dhcp
# Install missing selected management features:
.\Install-RelayPrerequisites.ps1 -Tools ActiveDirectory,Dns,Dhcp -Apply
# Optional feature payload source from matching Windows media:
.\Install-RelayPrerequisites.ps1 -Tools ActiveDirectory -Apply -Source 'D:\sources\sxs'
```

Use `-Python 'C:\Program Files\Python313\python.exe'` to select the actual
all-users Python installation. Missing Python is reported separately, and is
not downloaded by this script. `-Apply -WhatIf` previews installation.

| Tool selection | Windows feature | Purpose |
| --- | --- | --- |
| ActiveDirectory | RSAT-AD-PowerShell | AD queries and existing gMSA checks |
| Dns | RSAT-DNS-Server | DNS management cmdlets |
| Dhcp | RSAT-DHCP | DHCP management cmdlets |
| GpoBuilder | RSAT-AD-PowerShell, GPMC | Separate domain management host creating the deployment GPO |

The prerequisite script installs management tools, not DNS/DHCP/AD server roles.
It skips installed features and reports servicing errors and restart requirements.
It never automatically reboots, joins a domain, grants AD privileges, enables
WinRM listeners, changes execution policy, or creates a relay task. Reboot when
required and rerun the check. Feature payload access follows Windows servicing
policy; use matching media if your server cannot retrieve the payload.

Microsoft references: [RSAT installation](https://learn.microsoft.com/en-us/windows-server/administration/install-remote-server-administration-tools)
and [Install-WindowsFeature](https://learn.microsoft.com/en-us/powershell/module/servermanager/install-windowsfeature?view=windowsserver2025-ps).

## Reviewed Windows deployment

The remaining kit is under development. `New-RelayGpo.ps1` previews a deployment
to selected dedicated computers in an explicit OU. `-Create` prepares a private
SMB share and a new, unlinked GPO; `Enable-RelayGpo.ps1` is a separate activation
step. Do not activate until scripts, scope, signing policy and task identity have
been reviewed in a disposable domain. The GPO builder's host needs domain RSAT
tools; individual relay hosts need only tools used by their jobs.

Use unique relay tokens per host, entered privately in the builder. Tokens belong
in private per-computer share folders, never SYSVOL. SYSTEM is for local jobs;
remote work needs an existing appropriately delegated gMSA or machine identity.
The kit does not provision domain permissions. Signing or modifying kit scripts
requires deliberately regenerating the corresponding `kit.json` file hashes.

`Install-Relay.ps1 -Deployment <reviewed-json>` previews the task deployment;
`-Apply` installs it. Its `--check` heartbeat never claims or executes a job.
Verify the actual scheduled-task account, logs and last check-in in Settings >
Relays before assigning jobs. Unlink the GPO before removing a managed relay;
revoke its token in Layer 8. `Remove-Relay.ps1` requires a managed deployment
marker and supports preview with `-WhatIf`.

## Appliance relay

The optional Linux installer previews by default and requires explicit `--apply`
as root. It uses a dedicated restricted account and systemd service, separate
from the application's credentials. Live systemd deployment validation is pending;
review this deployment in a disposable appliance before production use.
