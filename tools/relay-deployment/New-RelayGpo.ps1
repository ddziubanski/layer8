# SPDX-License-Identifier: AGPL-3.0-or-later
# Run on a domain-joined Windows file/management server with AD + GroupPolicy + SMB tools.
# Default is a read-only plan. -Create provisions a NEW, UNLINKED GPO and private share.
[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param(
    [string]$Url,
    [Parameter(Mandatory=$true)][ValidateCount(1,20)][string[]]$Computers,
    [Parameter(Mandatory=$true)][string]$TargetOu,
    [string]$GpoName = 'Layer 8 - dedicated relays',
    [string]$ShareName = 'Layer8Relays$',
    [string]$SharePath = 'C:\Layer8RelayDeployment',
    [string]$Python = 'C:\Program Files\Python313\python.exe',
    [string]$Identity = 'SYSTEM',
    [string]$CaFile,
    [switch]$Create
)
. (Join-Path $PSScriptRoot 'Relay.Common.ps1')
$kit = Test-RelayKit $PSScriptRoot
if (-not $Url) { $Url = [string]$kit.portal_url }
$urlValue = Assert-RelayUrl $Url
$pythonValue = Assert-RelayLocalPath $Python
$local = Assert-RelayLocalPath $SharePath
if ($ShareName -notmatch '^[A-Za-z][A-Za-z0-9_-]{0,60}\$$' -or $GpoName.Length -gt 100 -or $GpoName -match '[\r\n]') { throw 'Use a short GPO name and a dedicated hidden share name ending in $.' }
if ($Identity -ne 'SYSTEM' -and $Identity -notmatch '^[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+\$$') { throw 'Use SYSTEM or an existing DOMAIN\gMSA$.' }
if ($Identity -eq 'SYSTEM') { Write-Warning 'SYSTEM is for local jobs. Remote WinRM/AD jobs need an appropriately delegated gMSA or machine account; this wizard grants neither.' }
Import-Module ActiveDirectory -ErrorAction Stop
Import-Module GroupPolicy -ErrorAction Stop
Import-Module SmbShare -ErrorAction Stop
$domain = Get-ADDomain
$dc = $domain.PDCEmulator
$ou = Get-ADOrganizationalUnit -Identity $TargetOu -Server $dc
if (-not $ou.DistinguishedName.StartsWith('OU=',[StringComparison]::OrdinalIgnoreCase)) { throw 'Target an explicit organizational unit, never the domain root.' }
$names = @($Computers | ForEach-Object {
    if ($_ -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,14}$') { throw 'Use dedicated relay NetBIOS computer names, at most 15 characters.' }
    $_.ToUpperInvariant()
} | Select-Object -Unique)
$accounts = @($names | ForEach-Object {
    $computer = Get-ADComputer -Identity $_ -Server $dc -Properties DNSHostName
    if (-not $computer.Enabled -or -not $computer.DistinguishedName.EndsWith(','+$ou.DistinguishedName,[StringComparison]::OrdinalIgnoreCase)) { throw 'Each enabled relay computer must be inside the reviewed target OU.' }
    $computer
})
$server = [Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName
if ($server -notmatch '^[A-Za-z0-9.-]+$') { throw 'Invalid deployment server name.' }
$unc = '\\' + $server + '\' + $ShareName
$startup = $unc + '\Public\Bootstrap.cmd'
if ($startup.Length -ge 260) { throw 'Startup script UNC is too long.' }
if (Get-GPO -Name $GpoName -Domain $domain.DNSRoot -Server $dc -ErrorAction SilentlyContinue) { throw 'That GPO exists. Choose a new name; existing policies are never edited.' }
if ((Test-Path -LiteralPath $local) -or (Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue)) { throw 'Choose a new deployment directory and share. Existing content/shares are never overwritten.' }
$plan = [ordered]@{format=1;url=$urlValue;computers=$names;target_ou=$ou.DistinguishedName;domain=$domain.DNSRoot;dc=$dc;identity=$Identity;python=$pythonValue;share=$unc;local_path=$local;gpo_name=$GpoName;linked=$false}
$plan | ConvertTo-Json -Depth 4 | Write-Output
if (-not $Create -or -not $PSCmdlet.ShouldProcess(($domain.DNSRoot+' / '+$unc),'Create an unlinked, computer-filtered relay GPO and private per-host token share')) { return }
New-Item -ItemType Directory -Path $local | Out-Null
Set-RelayAcl $local
$public = New-Item -ItemType Directory -Path (Join-Path $local 'Public')
$private = New-Item -ItemType Directory -Path (Join-Path $local 'Secrets')
$readers = @($accounts | ForEach-Object { $_.SID.Value })
Set-RelayAcl $local -Readers $readers
Set-RelayAcl $public.FullName -Readers $readers
Set-RelayAcl $private.FullName -Readers $readers
foreach ($name in @($kit.files.PSObject.Properties.Name) + 'kit.json') { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $public.FullName }
$config = [ordered]@{format=1;url=$urlValue;computers=$names;identity=$Identity;python=$pythonValue;secretRoot=($unc+'\Secrets');ca_file=$false;ca_sha256=''}
if ($CaFile) {
    $ca = Assert-RelayLocalPath $CaFile
    Copy-Item -LiteralPath $ca -Destination (Join-Path $public.FullName 'relay-ca.pem')
    $config.ca_file = $true
    $config.ca_sha256 = (Get-FileHash -LiteralPath $ca -Algorithm SHA256).Hash
}
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $public.FullName 'deployment.json') -Encoding UTF8
$bootstrap = '@echo off' + "`r`n" + '"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -File "%~dp0Install-Relay.ps1" -Deployment "%~dp0deployment.json" -Apply' + "`r`nexit /b %errorlevel%`r`n"
[IO.File]::WriteAllText((Join-Path $public.FullName 'Bootstrap.cmd'),$bootstrap,[Text.Encoding]::ASCII)
foreach ($computer in $accounts) {
    $folder = New-Item -ItemType Directory -Path (Join-Path $private.FullName $computer.Name.ToUpperInvariant())
    Set-RelayAcl $folder.FullName -Readers @($computer.SID.Value)
    $token = Read-RelayToken $computer.Name
    $tokenPath = Join-Path $folder.FullName 'token'
    [IO.File]::WriteAllText($tokenPath,$token,[Text.Encoding]::ASCII)
    Set-RelayAcl $tokenPath -Readers @($computer.SID.Value)
    $token = $null
}
$administrators = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')).Translate([Security.Principal.NTAccount]).Value
$shareReaders = @($accounts | ForEach-Object { $domain.NetBIOSName + '\' + $_.SamAccountName })
New-SmbShare -Name $ShareName -Path $local -FullAccess $administrators -ReadAccess $shareReaders -EncryptData $true -CachingMode None -FolderEnumerationMode AccessBased | Out-Null
$gpo = New-GPO -Name $GpoName -Domain $domain.DNSRoot -Server $dc -Comment 'Layer 8 dedicated relay deployment; generated unlinked; contains no tokens/passwords.'
$id = '{' + $gpo.Id.ToString().ToUpperInvariant() + '}'
$policy = '\\' + $dc + '\SYSVOL\' + $domain.DNSRoot + '\Policies\' + $id
$dn = 'CN=' + $id + ',CN=Policies,CN=System,' + $domain.DistinguishedName
# MS-GPSCR startup script format, UTF-16LE+BOM. No token is written to SYSVOL.
$scripts = Join-Path $policy 'Machine\Scripts'
New-Item -ItemType Directory -Path $scripts -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $scripts 'scripts.ini'),("[Startup]`r`n0CmdLine="+$startup+"`r`n0Parameters=`r`n"),[Text.Encoding]::Unicode)
# Register the Scripts CSE on this NEW GPO; a supported registry-policy write below
# performs the synchronized AD/SYSVOL machine-version increment on the same DC.
$scriptExtension = '[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]'
Set-ADObject -Identity $dn -Server $dc -Replace @{gPCMachineExtensionNames=$scriptExtension}
Set-GPRegistryValue -Guid $gpo.Id -Domain $domain.DNSRoot -Server $dc -Key 'HKLM\Software\Policies\Layer8\Relay' -ValueName 'DeploymentVersion' -Type String -Value ([DateTime]::UtcNow.ToString('O')) | Out-Null
$authenticated = (New-Object Security.Principal.SecurityIdentifier('S-1-5-11')).Translate([Security.Principal.NTAccount]).Value
Set-GPPermission -Guid $gpo.Id -DomainName $domain.DNSRoot -Server $dc -TargetName $authenticated -TargetType Group -PermissionLevel GpoRead -Replace | Out-Null
foreach ($computer in $accounts) { Set-GPPermission -Guid $gpo.Id -DomainName $domain.DNSRoot -Server $dc -TargetName $computer.SamAccountName -TargetType Computer -PermissionLevel GpoApply | Out-Null }
$ad = Get-ADObject -Identity $dn -Server $dc -Properties versionNumber,gPCMachineExtensionNames
$gpt = [IO.File]::ReadAllText((Join-Path $policy 'gpt.ini'))
if ($ad.gPCMachineExtensionNames -notlike '*42B5FAAE-6536-11D2-AE5A-0000F87571E3*' -or $gpt -notmatch '(?im)^Version=(\d+)\s*$' -or [int]$Matches[1] -ne [int]$ad.versionNumber -or [int]$ad.versionNumber -eq 0) { throw 'GPO extension/version verification failed. GPO remains unlinked; inspect it before use.' }
$plan.gpo_id = $gpo.Id.ToString()
$plan | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $local 'plan.json') -Encoding UTF8
Get-GPOReport -Guid $gpo.Id -Domain $domain.DNSRoot -Server $dc -ReportType Html -Path (Join-Path $local 'gpo-review.html')
Write-Output "Created UNLINKED GPO $($gpo.Id). Review $local\gpo-review.html, confirm the scripts meet your domain signing policy, then use Enable-RelayGpo.ps1 -Plan '$local\plan.json'."
