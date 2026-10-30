# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer 8 test lab, step 2 of 4. Run in an ADMIN PowerShell after the reboot.
# Creates a private lab network 10.88.0.0/24 with outbound internet through NAT.
# Lab VMs can't be reached from your real network, and they never see your real domain.
$ErrorActionPreference = 'Stop'
if (-not (Get-VMSwitch -Name L8Lab -ErrorAction SilentlyContinue)) {
    New-VMSwitch -Name L8Lab -SwitchType Internal | Out-Null
}
$if = Get-NetAdapter | Where-Object Name -eq 'vEthernet (L8Lab)'
if (-not (Get-NetIPAddress -InterfaceIndex $if.ifIndex -IPAddress 10.88.0.1 -ErrorAction SilentlyContinue)) {
    New-NetIPAddress -InterfaceIndex $if.ifIndex -IPAddress 10.88.0.1 -PrefixLength 24 | Out-Null
}
if (-not (Get-NetNat -Name L8LabNat -ErrorAction SilentlyContinue)) {
    New-NetNat -Name L8LabNat -InternalIPInterfaceAddressPrefix 10.88.0.0/24 | Out-Null
}
Get-VMSwitch L8Lab | Format-Table Name, SwitchType
Get-NetNat L8LabNat | Format-Table Name, InternalIPInterfaceAddressPrefix
Write-Host "Lab network ready."
