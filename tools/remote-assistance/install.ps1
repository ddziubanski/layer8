<#
  Registers the service desk launcher for helpdesk-remote: links on a technician's PC.
  Run as administrator (or deploy the same registry values and file by GPO):

    .\install.ps1 -ServiceDeskUrl https://helpdesk.corp.example
#>
param([Parameter(Mandatory = $true)][string]$ServiceDeskUrl)
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:ProgramFiles 'HelpdeskRemote'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'helpdesk-remote.ps1') -Destination $dir -Force

New-Item -Force -Path 'HKLM:\SOFTWARE\HelpdeskRemote' | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\HelpdeskRemote' -Name ServiceDeskUrl -Value $ServiceDeskUrl.TrimEnd('/')

$key = 'HKLM:\SOFTWARE\Classes\helpdesk-remote'
New-Item -Force -Path "$key\shell\open\command" | Out-Null
Set-ItemProperty -Path $key -Name '(Default)' -Value 'URL:Service desk remote session'
Set-ItemProperty -Path $key -Name 'URL Protocol' -Value ''
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Set-ItemProperty -Path "$key\shell\open\command" -Name '(Default)' -Value "`"$ps`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\helpdesk-remote.ps1`" `"%1`""
Write-Host "Registered helpdesk-remote: links for $ServiceDeskUrl"
