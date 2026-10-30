# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer 8 test lab, step 1 of 4. Run in an ADMIN PowerShell. Reboots at the end.
# Turns on Hyper-V and lets the current user manage VMs without admin rights
# (so the build scripts, run as you from Windows or WSL, can create and control the lab VMs).
$ErrorActionPreference = 'Stop'
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart
Add-LocalGroupMember -Group 'Hyper-V Administrators' -Member "$env:USERDOMAIN\$env:USERNAME" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force C:\isos, C:\layer8-lab\vms | Out-Null
Write-Host "Done. Rebooting in 15 seconds (Ctrl+C to reboot later yourself)..."
Start-Sleep 15
Restart-Computer -Force
