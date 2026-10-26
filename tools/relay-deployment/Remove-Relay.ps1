# SPDX-License-Identifier: AGPL-3.0-or-later
[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param([switch]$RemoveFiles)
. (Join-Path $PSScriptRoot 'Relay.Common.ps1')
$root = Assert-RelayLocalPath (Join-Path $env:ProgramData 'Layer8Relay')
$marker = Join-Path $root 'deployment.json'
if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).format -ne 1) { throw 'No managed Layer 8 relay deployment was found.' }
if (-not $PSCmdlet.ShouldProcess($root,'Stop/unregister Layer8Relay; remove managed files only when requested')) { return }
Stop-ScheduledTask -TaskName 'Layer8Relay' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'Layer8Relay' -Confirm:$false -ErrorAction SilentlyContinue
if ($RemoveFiles) { Remove-Item -LiteralPath $root -Recurse -Force }
Write-Output 'Relay task removed. Unlink its deployment GPO to prevent reinstallation, and revoke the relay token in Layer 8.'
