# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Builds a new, unlinked "Layer 8 relay" GPO that installs the relay at startup on chosen computers.

.DESCRIPTION
  Preview by default: it checks everything and says what it would do. With -Apply it:
    - creates a NEW GPO (it refuses if one with that name exists; it never changes existing GPOs);
    - filters it to one AD computer group: that group gets "Apply", Authenticated Users keep only
      "Read" (so the GPO stays readable but applies to nobody else);
    - adds one computer startup script, Install-Layer8Relay.ps1, with the kit and token share paths
      (and the gMSA name, if given) as its parameters. Nothing secret goes into SYSVOL: each host
      reads its own token from the token share (Set-Layer8RelayToken.ps1).
  It does NOT link the GPO anywhere, enable WinRM, change execution policy or grant any rights.
  Activating it is a separate, explicit step you take: link it to the OU holding the relay hosts
  (New-GPLink, shown at the end). Run as a domain administrator, with the GroupPolicy and
  ActiveDirectory modules (RSAT).

  Before applying: a kit share (read-only to the relay hosts) holding the unzipped relay kit
  (helpdesk_runner.py, relay.json, optional ca.pem), and a token share where each host's token file
  is readable only by that host (Set-Layer8RelayToken.ps1). PowerShell startup scripts run under
  the computers' execution policy: sign Install-Layer8Relay.ps1 or allow it, as your policy says.

.EXAMPLE
  .\New-Layer8RelayGpo.ps1 -ComputerGroup 'Layer 8 relay hosts' -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$
.EXAMPLE
  .\New-Layer8RelayGpo.ps1 -ComputerGroup 'Layer 8 relay hosts' -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$ -ServiceAccount 'CORP\gmsa-relay$' -Apply
#>
[CmdletBinding()]
param(
    [string]$Name = 'Layer 8 relay',
    [Parameter(Mandatory)][string]$ComputerGroup,
    [Parameter(Mandatory)][string]$KitPath,
    [Parameter(Mandatory)][string]$TokenPath,
    [string]$ServiceAccount,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Import-Module GroupPolicy
Import-Module ActiveDirectory
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $here 'Install-Layer8Relay.ps1'
# The scripts client-side extension, with the GPO editor's tool extension for scripts.
$ScriptsCse = '[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]'

if (Get-GPO -Name $Name -ErrorAction SilentlyContinue) {
    Write-Warning "A GPO called '$Name' already exists; this script never changes existing GPOs. Pick another -Name."
    exit 1
}
$problems = @()
$group = Get-ADGroup -Filter "Name -eq '$($ComputerGroup -replace "'", "''")'" -ErrorAction SilentlyContinue
if (-not $group) { $problems += "No AD group '$ComputerGroup'. Make a group of the relay computers first." }
foreach ($path in @($KitPath, $TokenPath)) {
    if ($path -notmatch '^\\\\[^\\]+\\[^\\]+') { $problems += "$path isn't a UNC share path (\\server\share)." }
    elseif (-not (Test-Path $path)) { $problems += "Can't reach $path from here." }
}
if ((Test-Path $KitPath) -and -not (Test-Path (Join-Path $KitPath 'helpdesk_runner.py'))) { $problems += "$KitPath doesn't hold the relay kit (helpdesk_runner.py)." }
if ((Test-Path $KitPath) -and -not (Test-Path (Join-Path $KitPath 'relay.json'))) { $problems += "$KitPath has no relay.json (from the downloaded kit)." }
if ($ServiceAccount -and -not $ServiceAccount.EndsWith('$')) { $problems += "$ServiceAccount isn't a gMSA (they end in `$)." }
if (-not (Test-Path $installer)) { $problems += "Install-Layer8Relay.ps1 isn't next to this script." }

$params = "-KitPath `"$KitPath`" -TokenPath `"$TokenPath`"" + $(if ($ServiceAccount) { " -ServiceAccount `"$ServiceAccount`"" } else { '' })
Write-Output "New GPO        : $Name (unlinked)"
Write-Output "Applies to     : members of '$ComputerGroup' only (Authenticated Users: read only)"
Write-Output "Startup script : Install-Layer8Relay.ps1 $params"
Write-Output "Runs relay as  : $(if ($ServiceAccount) { $ServiceAccount } else { 'SYSTEM (local work only)' })"
if ($problems) { $problems | ForEach-Object { Write-Warning $_ }; exit 1 }
if (-not $Apply) { Write-Output 'Preview only: nothing changed. Add -Apply to create the GPO.'; exit 0 }

$gpo = New-GPO -Name $Name -Comment 'Installs the Layer 8 relay at startup (New-Layer8RelayGpo.ps1). Holds no secrets.'
Set-GPPermission -Guid $gpo.Id -TargetName 'Authenticated Users' -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
Set-GPPermission -Guid $gpo.Id -TargetName $group.Name -TargetType Group -PermissionLevel GpoApply | Out-Null

$domain = (Get-ADDomain).DNSRoot
$machine = "\\$domain\SYSVOL\$domain\Policies\{$($gpo.Id)}\Machine"
$startup = Join-Path $machine 'Scripts\Startup'
New-Item -ItemType Directory -Path $startup -Force | Out-Null
Copy-Item -Path $installer -Destination $startup
# psscripts.ini is UTF-16 with a blank first line, as the GPO editor writes it.
$ini = "`r`n[Startup]`r`n0CmdLine=Install-Layer8Relay.ps1`r`n0Parameters=$params`r`n"
[IO.File]::WriteAllText((Join-Path $machine 'Scripts\psscripts.ini'), $ini, [Text.Encoding]::Unicode)
(Get-Item (Join-Path $machine 'Scripts\psscripts.ini') -Force).Attributes = 'Hidden'

# Tell clients the GPO now has machine scripts, and bump its version so they pick it up.
$adObject = "CN={$($gpo.Id)},CN=Policies,CN=System,$((Get-ADDomain).DistinguishedName)"
$version = [int](Get-ADObject -Identity $adObject -Properties versionNumber).versionNumber + 1
Set-ADObject -Identity $adObject -Replace @{ gPCMachineExtensionNames = $ScriptsCse; versionNumber = $version }
$gpt = "\\$domain\SYSVOL\$domain\Policies\{$($gpo.Id)}\GPT.INI"
Set-Content -Path $gpt -Value "[General]`r`nVersion=$version`r`n" -Encoding ascii

Write-Output "Created '$Name' ({$($gpo.Id)}), unlinked."
Write-Output 'Next, when the tokens are on the token share (Set-Layer8RelayToken.ps1), activate it where the relay hosts are:'
# The containers holding the group's computers now; the filter keeps everyone else in them out.
$containers = @(Get-ADGroupMember -Identity $group | Where-Object objectClass -eq 'computer' |
    ForEach-Object { ($_.distinguishedName -split '(?<!\\),', 2)[1] } | Sort-Object -Unique)
if (-not $containers) { Write-Output "  ('$ComputerGroup' has no computers yet.) New-GPLink -Name '$Name' -Target '<OU of the relay hosts>'" }
foreach ($ou in $containers) {
    if ($ou -like 'CN=*') { Write-Output "  ($ou is a container, which GPOs can't be linked to: move the relay hosts into an OU first.)" }
    else { Write-Output "  New-GPLink -Name '$Name' -Target '$ou'" }
}
Write-Output 'The hosts install the relay at their next restart (or run gpupdate /force and restart).'
