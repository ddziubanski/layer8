# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Installs or updates the Layer 8 relay on this computer. Run at startup by the "Layer 8 relay" GPO
  (New-Layer8RelayGpo.ps1), as SYSTEM; can also be run by hand as an administrator.

.DESCRIPTION
  Holds no secrets. It copies helpdesk_runner.py (and the CA certificate, if the kit has one) from
  the read-only kit share, and this computer's own token from the token share, where only this
  computer's account can read <COMPUTERNAME>.token (Set-Layer8RelayToken.ps1). It checks in once
  without taking work; only if that succeeds does it register the "Layer 8 relay" scheduled task,
  which starts at boot as SYSTEM (scripts that run on this host) or as an existing gMSA (scripts
  that reach other computers over WinRM). Running it again only changes what's out of date.
  It doesn't enable WinRM, change execution policy or grant any domain rights.

  Python 3.10+ must already be installed for all users.

.EXAMPLE
  .\Install-Layer8Relay.ps1 -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$
.EXAMPLE
  .\Install-Layer8Relay.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$KitPath,
    [string]$TokenPath,
    # An existing gMSA, e.g. CORP\gmsa-relay$ (installed on this host with Install-ADServiceAccount).
    [string]$ServiceAccount,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Home8 = Join-Path $env:ProgramData 'Layer8Relay'
$TaskName = 'Layer 8 relay'
$Log = Join-Path $Home8 'install.log'

function Write-Log([string]$Text) {
    if (-not (Test-Path $Home8)) { New-Item -ItemType Directory -Path $Home8 -Force | Out-Null }
    "$(Get-Date -Format s)  $Text" | Out-File -FilePath $Log -Append -Encoding utf8
    Write-Output $Text
}

function Set-PrivateAcl([string]$Path, [string[]]$Readers) {
    # Only SYSTEM and Administrators (plus the given readers); nothing inherited.
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    if (Test-Path $Path -PathType Leaf) { $acl = New-Object System.Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = if (Test-Path $Path -PathType Container) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    foreach ($who in @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($who, 'FullControl', $inherit, 'None', 'Allow')))
    }
    foreach ($who in $Readers) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($who, 'ReadAndExecute', $inherit, 'None', 'Allow')))
    }
    Set-Acl -Path $Path -AclObject $acl
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    if (Test-Path $Home8) { Remove-Item -Recurse -Force $Home8 }
    Write-Output 'Removed the Layer 8 relay task and files. Delete or revoke the relay in Settings > Relays too.'
    exit 0
}

if (-not $KitPath -or -not $TokenPath) { throw 'Give -KitPath and -TokenPath (or -Uninstall).' }

# Python 3.10+ (the relay uses only its standard library).
$python = $null
foreach ($candidate in @('py.exe', 'python.exe')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $args310 = if ($candidate -eq 'py.exe') { @('-3', '-c', 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 10))') } else { @('-c', 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 10))') }
    $exe = & $cmd.Source @args310 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { $python = ($exe | Select-Object -Last 1).Trim(); break }
}
if (-not $python) { Write-Log 'Python 3.10 or later for all users is required; nothing installed.'; exit 1 }

$config = Get-Content -Raw -Path (Join-Path $KitPath 'relay.json') | ConvertFrom-Json
if (-not ($config.url -match '^https://')) { Write-Log 'relay.json needs the service desk https:// address.'; exit 1 }

$readers = @()
if ($ServiceAccount) { $readers += $ServiceAccount }
if (-not (Test-Path $Home8)) { New-Item -ItemType Directory -Path $Home8 -Force | Out-Null }
Set-PrivateAcl -Path $Home8 -Readers $readers

# The program and the CA certificate, when they changed.
foreach ($name in @('helpdesk_runner.py', 'ca.pem')) {
    $from = Join-Path $KitPath $name
    $to = Join-Path $Home8 $name
    if (-not (Test-Path $from)) { continue }
    if (-not (Test-Path $to) -or (Get-FileHash $from).Hash -ne (Get-FileHash $to).Hash) {
        Copy-Item -Path $from -Destination $to -Force
        Write-Log "Updated $name"
    }
}

# This computer's token: only this computer's account can read it on the share.
$tokenFrom = Join-Path $TokenPath "$($env:COMPUTERNAME).token"
$tokenTo = Join-Path $Home8 'token'
if (Test-Path $tokenFrom) {
    $token = (Get-Content -Raw -Path $tokenFrom).Trim()
    if (-not $token.StartsWith('hdr_')) { Write-Log "$tokenFrom doesn't hold a relay token (hdr_...)."; exit 1 }
    Set-Content -Path $tokenTo -Value $token -NoNewline -Encoding ascii
    Set-PrivateAcl -Path $tokenTo -Readers $readers
} elseif (-not (Test-Path $tokenTo)) {
    Write-Log "No token for $($env:COMPUTERNAME) at $tokenFrom yet; nothing installed."
    exit 1
}

$runArgs = @('-I', (Join-Path $Home8 'helpdesk_runner.py'), '--url', $config.url, '--token-file', $tokenTo)
if (Test-Path (Join-Path $Home8 'ca.pem')) { $runArgs += @('--ca-file', (Join-Path $Home8 'ca.pem')) }

# Prove the address, certificate and token before leaving it running (claims no work).
$check = & $python @runArgs --check 2>&1
if ($LASTEXITCODE -ne 0) { Write-Log "Check-in failed: $check"; exit 1 }
Write-Log "$check"

$quoted = ($runArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
$action = New-ScheduledTaskAction -Execute $python -Argument $quoted -WorkingDirectory $Home8
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = if ($ServiceAccount) {
    New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password -RunLevel Limited
} else {
    New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Log "Layer 8 relay task registered and started as $(if ($ServiceAccount) { $ServiceAccount } else { 'SYSTEM' })."
