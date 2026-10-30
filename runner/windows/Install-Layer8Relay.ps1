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
  It doesn't enable WinRM, change execution policy or grant any domain rights. It logs what it did
  to C:\ProgramData\Layer8Relay\install.log; the relay itself logs to state\relay.log.

  Python 3.10+ must already be installed for all users.

.EXAMPLE
  .\Install-Layer8Relay.ps1 -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$
.EXAMPLE
  .\Install-Layer8Relay.ps1 -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$ -ServiceAccount 'CORP\gmsa-relay$' -Check
.EXAMPLE
  .\Install-Layer8Relay.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$KitPath,
    [string]$TokenPath,
    # An existing gMSA, e.g. CORP\gmsa-relay$ (installed on this host with Install-ADServiceAccount).
    [string]$ServiceAccount,
    # Only check whether an install would work here (Python, kit, token, gMSA, check-in); change nothing.
    [switch]$Check,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Home8 = Join-Path $env:ProgramData 'Layer8Relay'
$TaskName = 'Layer 8 relay'
$Log = Join-Path $Home8 'install.log'
# The relay's own log; the only place the task's account can write.
$State = Join-Path $Home8 'state'

function Write-Log([string]$Text) {
    if (-not (Test-Path $Home8)) { New-Item -ItemType Directory -Path $Home8 -Force | Out-Null }
    "$(Get-Date -Format s)  $Text" | Out-File -FilePath $Log -Append -Encoding utf8
    Write-Output $Text
}

# A startup script has no console: whatever goes wrong must end up in install.log.
trap { Write-Log "Failed: $_ (line $($_.InvocationInfo.ScriptLineNumber))"; exit 1 }

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

# Python 3.10+ (the relay uses only its standard library).
function Find-Python {
    foreach ($candidate in @('py.exe', 'python.exe')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $args310 = if ($candidate -eq 'py.exe') { @('-3', '-c', 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 10))') } else { @('-c', 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 10))') }
        $exe = & $cmd.Source @args310 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return ($exe | Select-Object -Last 1).Trim() }
    }
    return $null
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

if ($Check) {
    # For an administrator on a relay host: would an install work here? Changes nothing.
    $rows = New-Object System.Collections.Generic.List[object]
    function Add-Row([string]$What, [bool]$Ok, [string]$Detail) { $rows.Add([pscustomobject]@{ Check = $What; OK = $Ok; Detail = $Detail }) }
    $python = Find-Python
    Add-Row 'Python 3.10+ for all users' ([bool]$python) $(if ($python) { $python } else { 'not found: install Python for all users' })
    $config = $null
    $kitOk = (Test-Path (Join-Path $KitPath 'helpdesk_runner.py')) -and (Test-Path (Join-Path $KitPath 'relay.json'))
    Add-Row 'Relay kit on the kit share' $kitOk $(if ($kitOk) { $KitPath } else { "helpdesk_runner.py or relay.json missing in $KitPath" })
    if ($kitOk) {
        $config = Get-Content -Raw -Path (Join-Path $KitPath 'relay.json') | ConvertFrom-Json
        Add-Row 'Service desk address' ($config.url -match '^https://') "$($config.url)"
    }
    $tokenFile = Join-Path $TokenPath "$($env:COMPUTERNAME).token"
    $token = if (Test-Path $tokenFile) { (Get-Content -Raw -Path $tokenFile).Trim() } else { '' }
    Add-Row "This host's token" ($token.StartsWith('hdr_')) $(if ($token.StartsWith('hdr_')) { $tokenFile } elseif ($token) { "$tokenFile doesn't hold a relay token" } else { "no $tokenFile yet (Set-Layer8RelayToken.ps1)" })
    if ($ServiceAccount) {
        if (Get-Command Test-ADServiceAccount -ErrorAction SilentlyContinue) {
            $sam = ($ServiceAccount -split '\\')[-1]
            $gmsaOk = [bool](Test-ADServiceAccount -Identity $sam -ErrorAction SilentlyContinue)
            Add-Row "gMSA $ServiceAccount" $gmsaOk $(if ($gmsaOk) { 'installed and usable on this host' } else { 'not usable here: Install-ADServiceAccount, and allow this computer to retrieve its password' })
        } else {
            Add-Row "gMSA $ServiceAccount" $false "can't check: install RSAT-AD-PowerShell, or check it yourself with Test-ADServiceAccount"
        }
    }
    if ($python -and $config -and $token.StartsWith('hdr_')) {
        # The same no-work check-in the install does, with a temporary copy of the token.
        $tmp = [IO.Path]::GetTempFileName()
        try {
            Set-Content -Path $tmp -Value $token -NoNewline -Encoding ascii
            $runner = Join-Path $KitPath 'helpdesk_runner.py'
            $caArgs = if (Test-Path (Join-Path $KitPath 'ca.pem')) { @('--ca-file', (Join-Path $KitPath 'ca.pem')) } else { @() }
            $out = & $python -I $runner --url $config.url --token-file $tmp @caArgs --check 2>&1
            Add-Row 'Check-in (claims no work)' ($LASTEXITCODE -eq 0) "$out"
        } finally { Remove-Item $tmp -Force }
    }
    $rows | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Output
    if ($rows | Where-Object { -not $_.OK }) { Write-Output 'Not ready: fix the items marked False. Nothing was changed.'; exit 1 }
    Write-Output 'Ready: the GPO (or running this without -Check) would install the relay here. Nothing was changed.'
    exit 0
}

$python = Find-Python
if (-not $python) { Write-Log 'Python 3.10 or later for all users is required; nothing installed.'; exit 1 }

$config = Get-Content -Raw -Path (Join-Path $KitPath 'relay.json') | ConvertFrom-Json
if (-not ($config.url -match '^https://')) { Write-Log 'relay.json needs the service desk https:// address.'; exit 1 }

$readers = @()
if ($ServiceAccount) { $readers += $ServiceAccount }
if (-not (Test-Path $Home8)) { New-Item -ItemType Directory -Path $Home8 -Force | Out-Null }
Set-PrivateAcl -Path $Home8 -Readers $readers
if (-not (Test-Path $State)) { New-Item -ItemType Directory -Path $State -Force | Out-Null }
if ($ServiceAccount) {
    $acl = Get-Acl $State
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($ServiceAccount, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    Set-Acl -Path $State -AclObject $acl
}

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
$checkIn = & $python @runArgs --check 2>&1
if ($LASTEXITCODE -ne 0) { Write-Log "Check-in failed: $checkIn"; exit 1 }
Write-Log "$checkIn"

$runArgs += @('--log-file', (Join-Path $State 'relay.log'))
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
