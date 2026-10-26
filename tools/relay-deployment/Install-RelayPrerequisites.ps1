# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
Checks a Windows Server relay and installs only explicitly selected management features.
.EXAMPLE
.\Install-RelayPrerequisites.ps1 -Tools ActiveDirectory,Dns -Apply
#>
[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [ValidateSet('ActiveDirectory','Dns','Dhcp','GpoBuilder')]
    [string[]]$Tools = @(),
    [string]$Python = 'C:\Program Files\Python313\python.exe',
    [string]$Source,
    [switch]$Apply
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance -ClassName Win32_OperatingSystem
if ($os.ProductType -eq 1 -or [version]$os.Version -lt [version]'10.0') {
    throw 'Run on Windows Server 2016 or newer, including Server Core.'
}
Import-Module ServerManager -ErrorAction Stop
$map = @{
    ActiveDirectory = @('RSAT-AD-PowerShell')
    Dns = @('RSAT-DNS-Server')
    Dhcp = @('RSAT-DHCP')
    GpoBuilder = @('RSAT-AD-PowerShell','GPMC')
}
$requested = @($Tools | ForEach-Object { $map[$_] } | Sort-Object -Unique)
$missing = @()
foreach ($name in $requested) {
    $feature = Get-WindowsFeature -Name $name
    if (-not $feature) { throw "Feature $name is unavailable on this Windows image." }
    if (-not $feature.Installed) { $missing += $name }
}
$pythonReady = $false
if (Test-Path -LiteralPath $Python -PathType Leaf) {
    & $Python -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'
    $pythonReady = $LASTEXITCODE -eq 0
}
$taskReady = [bool](Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)
[pscustomobject]@{
    OperatingSystem = $os.Caption
    SelectedTools = $Tools
    MissingFeatures = $missing
    PythonPath = $Python
    PythonReady = $pythonReady
    ScheduledTasksReady = $taskReady
    Mode = $(if ($Apply) { 'Apply' } else { 'Preview' })
} | Write-Output
if (-not $pythonReady) {
    Write-Warning 'Install trusted Python 3.10+ for all users and supply its python.exe path. Python is a separate prerequisite, not a Windows feature.'
}
if (-not $taskReady) { throw 'The built-in ScheduledTasks module is unavailable; repair the Windows image before installing the relay.' }
if ($missing.Count -eq 0) {
    Write-Output 'No Windows feature installation is needed for the selected tools.'
    return
}
if (-not $Apply -or -not $PSCmdlet.ShouldProcess($env:COMPUTERNAME,('Install management features: ' + ($missing -join ', ')))) { return }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run Windows PowerShell as administrator to install features.'
}
$arguments = @{Name=$missing;ErrorAction='Stop';Restart=$false}
if ($Source) { $arguments.Source = $Source }
$result = Install-WindowsFeature @arguments
$result | Write-Output
if (-not $result.Success) { throw 'Windows feature installation failed. Review the servicing result and rerun this script after resolving it.' }
if ([string]$result.RestartNeeded -eq 'Yes') {
    Write-Warning 'Windows requires a restart. Schedule it, then rerun this prerequisite check before installing the relay.'
    return
}
foreach ($name in $missing) {
    if (-not (Get-WindowsFeature -Name $name).Installed) { throw "Feature $name was not installed successfully." }
}
Write-Output 'Selected Windows management features verified. Rerun this check after installing Python or restarting Windows.'
