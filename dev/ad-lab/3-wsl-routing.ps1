# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer 8 test lab, step 3 of 4 (only if you work in WSL). Run in an ADMIN PowerShell.
# Lets WSL reach the lab network 10.88.0.0/24 by routing between the WSL and L8Lab switches.
# WSL recreates its adapter on every start, so a SYSTEM task re-applies this every 5 minutes.
# Undo: Unregister-ScheduledTask L8LabWslRouting; Set-NetIPInterface -Forwarding Disabled on both.
$ErrorActionPreference = 'Stop'
$apply = 'Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like ''vEthernet (WSL*'' -or $_.InterfaceAlias -eq ''vEthernet (L8Lab)'' } | Set-NetIPInterface -Forwarding Enabled'
Invoke-Expression $apply
$action = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -WindowStyle Hidden -Command `"$apply`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName L8LabWslRouting -Action $action -Trigger @($trigger, (New-ScheduledTaskTrigger -AtStartup)) -User SYSTEM -RunLevel Highest -Force | Out-Null
Get-NetIPInterface -AddressFamily IPv4 | Where-Object InterfaceAlias -like 'vEthernet*' | Format-Table InterfaceAlias, Forwarding
Write-Host "Done. WSL can now reach 10.88.0.0/24."
