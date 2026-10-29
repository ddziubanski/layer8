# SPDX-License-Identifier: AGPL-3.0-or-later
# DC01 first logon: add roles, queue stage 2 as SYSTEM, promote to the corp.example.com forest (reboots).
$ErrorActionPreference = 'Stop'
Start-Transcript -Append C:\lab\setup.log
$cfg = Get-Content -Raw C:\lab\lab.json | ConvertFrom-Json
Install-WindowsFeature AD-Domain-Services, DNS, DHCP, GPMC -IncludeManagementTools | Out-Null
$action = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\lab\stage2.ps1'
Register-ScheduledTask -TaskName L8LabStage2 -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -User SYSTEM -RunLevel Highest -Force | Out-Null
$dsrm = ConvertTo-SecureString $cfg.password -AsPlainText -Force
Install-ADDSForest -DomainName corp.example.com -DomainNetbiosName CORP -InstallDns -SafeModeAdministratorPassword $dsrm -Force -NoRebootOnCompletion:$false
