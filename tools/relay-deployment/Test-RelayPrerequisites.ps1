# SPDX-License-Identifier: AGPL-3.0-or-later
# Isolated command mocks: never installs features or contacts a domain.
$ErrorActionPreference = 'Stop'
$global:Layer8PrereqInstalled = $false
$global:Layer8PrereqUnavailable = $false
$global:Layer8PrereqQueried = @()
function Get-CimInstance { param($ClassName) [pscustomobject]@{ProductType=3;Version='10.0.26100';Caption='Test Windows Server'} }
function Import-Module { param($Name) }
function Get-WindowsFeature {
    param($Name)
    $global:Layer8PrereqQueried += $Name
    if (-not $global:Layer8PrereqUnavailable) { [pscustomobject]@{Name=$Name;Installed=$global:Layer8PrereqInstalled} }
}
function Install-WindowsFeature { throw 'TEST FAILURE: feature installation must never run in preview/WhatIf.' }
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
$installer = Join-Path $PSScriptRoot 'Install-RelayPrerequisites.ps1'
$missingPython = Join-Path $env:TEMP ('layer8-no-python-' + [guid]::NewGuid() + '.exe')
$result = @(& $installer -Python $missingPython -WarningAction SilentlyContinue)
Assert-True ($global:Layer8PrereqQueried.Count -eq 0) 'Basic relay must not request Windows features.'
Assert-True (-not $result[0].PythonReady) 'Missing Python must be reported.'
$result = @(& $installer -Tools ActiveDirectory,Dns,Dhcp,GpoBuilder -Python $missingPython -WarningAction SilentlyContinue)
Assert-True (($result[0].MissingFeatures -join ',') -eq 'GPMC,RSAT-AD-PowerShell,RSAT-DHCP,RSAT-DNS-Server') 'Only selected management tools should be requested, without duplicates.'
& $installer -Tools ActiveDirectory -Apply -WhatIf -Python $missingPython -WarningAction SilentlyContinue | Out-Null
$global:Layer8PrereqInstalled = $true
$result = @(& $installer -Tools ActiveDirectory -Apply -Python $missingPython -WarningAction SilentlyContinue)
Assert-True ($result[0].MissingFeatures.Count -eq 0) 'Installed features must be skipped.'
$global:Layer8PrereqUnavailable = $true
$failed = $false
try { & $installer -Tools Dns -Python $missingPython | Out-Null } catch { $failed = $_.Exception.Message -like '*unavailable*' }
Assert-True $failed 'Unavailable feature must fail before any installation.'
Write-Output 'Prerequisite safety checks passed: basic, selection, preview, WhatIf, idempotency, unavailable feature.'
