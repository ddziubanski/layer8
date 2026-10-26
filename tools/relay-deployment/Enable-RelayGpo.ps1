# SPDX-License-Identifier: AGPL-3.0-or-later
# Separate activation step, after review of the new policy and private share.
[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$Plan)
. (Join-Path $PSScriptRoot 'Relay.Common.ps1')
$data = Get-Content -LiteralPath $Plan -Raw | ConvertFrom-Json
if ($data.format -ne 1) { throw 'Unsupported deployment plan.' }
Import-Module ActiveDirectory -ErrorAction Stop
Import-Module GroupPolicy -ErrorAction Stop
$domain = Get-ADDomain
if ($data.domain -ne $domain.DNSRoot -or $data.target_ou -notlike 'OU=*') { throw 'The plan must target an explicit OU in this domain.' }
$ou = Get-ADOrganizationalUnit -Identity $data.target_ou -Server $data.dc
$gpo = Get-GPO -Guid ([Guid]$data.gpo_id) -Domain $data.domain -Server $data.dc
if ($gpo.DisplayName -ne $data.gpo_name -or $gpo.Description -notlike 'Layer 8 dedicated relay deployment;*') { throw 'The GPO no longer matches the reviewed deployment.' }
foreach ($name in $data.computers) {
    $computer = Get-ADComputer -Identity $name -Server $data.dc
    if (-not $computer.Enabled -or -not $computer.DistinguishedName.EndsWith(','+$ou.DistinguishedName,[StringComparison]::OrdinalIgnoreCase)) { throw 'A selected computer moved or was disabled. Review the policy again.' }
}
Write-Output "Activate $($gpo.DisplayName) only in $($ou.DistinguishedName), for: $($data.computers -join ', ')"
if ($PSCmdlet.ShouldProcess($ou.DistinguishedName,'Link the reviewed relay GPO (not enforced; inheritance unchanged)')) {
    New-GPLink -Guid $gpo.Id -Domain $data.domain -Server $data.dc -Target $ou.DistinguishedName -LinkEnabled Yes -Enforced No | Out-Null
    Write-Output 'GPO linked. Reboot one selected pilot host and verify Settings > Relays before rolling out further.'
}
