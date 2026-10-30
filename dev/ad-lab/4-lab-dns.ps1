# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer 8 test lab, step 4 of 4. Run in an ADMIN PowerShell.
# Sends lookups for *.corp.example.com to DC01, so the host and WSL resolve lab names
# (LDAPS checks the certificate against dc01.corp.example.com). Other names are untouched.
# Undo: Get-DnsClientNrptRule | Where-Object Namespace -eq '.corp.example.com' | Remove-DnsClientNrptRule -Force
if (-not (Get-DnsClientNrptRule | Where-Object Namespace -eq '.corp.example.com')) {
    Add-DnsClientNrptRule -Namespace '.corp.example.com' -NameServers 10.88.0.10 -Comment 'Layer 8 test lab'
}
Clear-DnsClientCache
Resolve-DnsName dc01.corp.example.com | Format-Table Name, IPAddress
