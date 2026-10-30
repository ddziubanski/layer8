# SPDX-License-Identifier: AGPL-3.0-or-later
# RELAY01 first logon (already joined to corp.example.com): Python for the relay, then publish readiness.
$ErrorActionPreference = 'Stop'
Start-Transcript -Append C:\lab\setup.log
$v = '3.12.10'
$exe = "C:\lab\python-$v-amd64.exe"
Invoke-WebRequest -UseBasicParsing "https://www.python.org/ftp/python/$v/python-$v-amd64.exe" -OutFile $exe
Start-Process -Wait $exe '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0'
& 'C:\Program Files\Python312\python.exe' --version
Remove-Item C:\lab\lab.json, $exe -Force -ErrorAction SilentlyContinue

Enable-NetFirewallRule -Name FPS-ICMP4-ERQ-In   # ping answers once the build is done
Write-Host "RELAY01 ready $(Get-Date -Format o)"   # the transcript holds setup.log open
