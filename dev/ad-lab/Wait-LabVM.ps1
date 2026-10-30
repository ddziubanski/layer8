# SPDX-License-Identifier: AGPL-3.0-or-later
# Wait until a lab VM has finished its unattended build.
param([Parameter(Mandatory)][ValidateSet('DC01', 'RELAY01', 'WS01')][string]$Name, [int]$Minutes = 60)
$until = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $until) {
    $ok = switch ($Name) {
        'DC01'    { [bool](Resolve-DnsName lab-ready.corp.example.com -Type TXT -Server 10.88.0.10 -DnsOnly -ErrorAction SilentlyContinue | Where-Object { $_.Type -eq 'TXT' -and "$($_.Strings)" -like 'DC01 ready*' }) }
        'RELAY01' { Test-Connection 10.88.0.20 -Count 1 -Quiet }
        'WS01'    { [bool](Resolve-DnsName ws01.corp.example.com -Type A -Server 10.88.0.10 -DnsOnly -ErrorAction SilentlyContinue | Where-Object Type -eq 'A') }
    }
    if ($ok) { Write-Host "$Name ready"; exit 0 }
    Start-Sleep 30
}
Write-Host "$Name not ready after $Minutes min"; exit 1
