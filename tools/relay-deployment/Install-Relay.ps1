# SPDX-License-Identifier: AGPL-3.0-or-later
# Runs on Server Core or Desktop Experience. No GUI, execution-policy changes or inbound ports.
[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Mandatory=$true)][string]$Deployment,
    [switch]$Apply
)
. (Join-Path $PSScriptRoot 'Relay.Common.ps1')
$config = Get-Content -LiteralPath $Deployment -Raw | ConvertFrom-Json
if ($config.format -ne 1 -or $env:COMPUTERNAME -notin @($config.computers)) { throw 'This computer is not in the reviewed deployment.' }
$url = Assert-RelayUrl $config.url
$python = Assert-RelayLocalPath $config.python
$root = Assert-RelayLocalPath (Join-Path $env:ProgramData 'Layer8Relay')
$kit = Test-RelayKit $PSScriptRoot
$identity = [string]$config.identity
if ($identity -ne 'SYSTEM' -and $identity -notmatch '^[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+\$$') { throw 'Use SYSTEM or an existing DOMAIN\gMSA$; passwords are never stored.' }
Write-Output "Relay plan: $env:COMPUTERNAME -> $url; identity $identity; task Layer8Relay; root $root"
if (-not $Apply -or -not $PSCmdlet.ShouldProcess($env:COMPUTERNAME,'Install/update the reviewed Layer 8 relay task')) { return }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as local administrator or through computer startup policy.' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Install Python 3.10+ for all users at the reviewed path first.' }
& $python -I -c 'import sys; assert sys.version_info >= (3,10)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
if ($identity -ne 'SYSTEM') {
    Import-Module ActiveDirectory -ErrorAction Stop
    $account = ($identity -split '\\')[1].TrimEnd('$')
    if (-not (Test-ADServiceAccount -Identity $account)) { throw 'The existing gMSA is not installed/authorized on this host. Have the domain administrator provision it first.' }
}
$task = Get-ScheduledTask -TaskName 'Layer8Relay' -ErrorAction SilentlyContinue
if ($task -and -not (Test-Path -LiteralPath (Join-Path $root 'deployment.json'))) { throw 'An unmanaged Layer8Relay task exists; inspect it locally.' }
$fingerprint = (Get-FileHash -LiteralPath $Deployment -Algorithm SHA256).Hash + ':' + $kit.files.'helpdesk_runner.py'
$marker = Join-Path $root 'deployment.json'
if (Test-Path -LiteralPath $root) {
    if (-not (Test-Path -LiteralPath $marker)) { throw 'Relay folder exists without a managed deployment marker; inspect locally.' }
    $old = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($task -and $task.State -eq 'Running' -and $old.fingerprint -ne $fingerprint) { throw 'The relay is running. Stop it during a maintenance window before changing its deployment.' }
}
$tokenSource = Join-Path ([string]$config.secretRoot) ($env:COMPUTERNAME + '\token')
$token = (Get-Content -LiteralPath $tokenSource -Raw).Trim()
if ($token -notmatch '^hdr_[A-Za-z0-9_-]{32,128}$') { throw 'No valid per-host token is available on the private deployment share.' }
if ($task -and $task.State -eq 'Running' -and (Get-Content -LiteralPath (Join-Path $root 'token') -Raw).Trim() -ne $token) { throw 'Token changed; stop the relay during a maintenance window before rotating it.' }
New-Item -ItemType Directory -Path $root -Force | Out-Null
$readers = if ($identity -eq 'SYSTEM') { @() } else { @($identity) }
Set-RelayAcl $root -Readers $readers
$state = Join-Path $root 'state'
New-Item -ItemType Directory -Path $state -Force | Out-Null
Set-RelayAcl $state -Writers $readers
$tokenFile = Join-Path $root 'token'
# Write a fresh credential only inside an already protected directory.
[IO.File]::WriteAllText($tokenFile,$token,[Text.Encoding]::ASCII)
Set-RelayAcl $tokenFile -Readers $readers
$token = $null
$runner = Join-Path $root 'helpdesk_runner.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'helpdesk_runner.py') -Destination $runner -Force
$arguments = @($runner,'--url',$url,'--token-file',$tokenFile,'--log-file',(Join-Path $state 'relay.log'))
if ($config.ca_file) {
    $caSource = Join-Path $PSScriptRoot 'relay-ca.pem'
    if ((Get-FileHash -LiteralPath $caSource -Algorithm SHA256).Hash -ne $config.ca_sha256) { throw 'Private CA checksum mismatch.' }
    $caTarget = Join-Path $root 'relay-ca.pem'
    Copy-Item -LiteralPath $caSource -Destination $caTarget -Force
    $arguments += @('--ca-file',$caTarget)
}
& $python @arguments --check
if ($LASTEXITCODE -ne 0) { throw 'Safe check-in failed; no new task was registered. Check HTTPS, the private CA and token.' }
# Repeated startup processing keeps an unchanged running relay intact.
if ($task -and $task.State -eq 'Running' -and $old.fingerprint -eq $fingerprint) { Write-Output 'Relay already running; safe check-in verified.'; return }
$quoted = @($arguments | ForEach-Object { if ($_ -match '["\r\n]') { throw 'Invalid relay argument.' }; '"' + $_ + '"' }) -join ' '
$action = New-ScheduledTaskAction -Execute $python -Argument $quoted
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT60S'
$principal = if ($identity -eq 'SYSTEM') { New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest } else { New-ScheduledTaskPrincipal -UserId $identity -LogonType Password -RunLevel Highest }
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'Layer8Relay' -Action $action -Trigger $trigger -Principal $principal -Settings $options -Description 'Layer 8 dedicated relay; outbound HTTPS only' -Force | Out-Null
@{format=1;fingerprint=$fingerprint;url=$url;identity=$identity} | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
Start-ScheduledTask -TaskName 'Layer8Relay'
Write-Output 'Relay task started. Verify its host/version and last check-in in Settings > Relays.'
