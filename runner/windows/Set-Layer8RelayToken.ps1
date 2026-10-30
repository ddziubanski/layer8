# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Puts one relay host's token on the token share, readable only by that computer's account.

.DESCRIPTION
  In Layer 8: Settings > Relays > Add a relay (one per host), and copy its token. Then run this as
  a domain administrator; it asks for the token (it's never on the command line or in SYSVOL) and
  writes <TokenPath>\<COMPUTER>.token with permissions for DOMAIN\COMPUTER$ (read) and
  Administrators only. For scripted rollouts, -TokenFile reads the token from a file instead
  (delete that file afterwards). To revoke, use Settings > Relays > New token (or delete the relay): the old
  token stops working at once. Delete the file too.

.EXAMPLE
  .\Set-Layer8RelayToken.ps1 -Computer RELAY01 -TokenPath \\fs01\layer8-relay-tokens$
.EXAMPLE
  .\Set-Layer8RelayToken.ps1 -Computer RELAY01 -TokenPath \\fs01\layer8-relay-tokens$ -TokenFile C:\Temp\relay01.token
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Computer,
    [Parameter(Mandatory)][string]$TokenPath,
    [string]$TokenFile
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
$account = Get-ADComputer -Identity $Computer
$domain = (Get-ADDomain).NetBIOSName
$who = "$domain\$($account.SamAccountName)"
if ($TokenFile) {
    $token = (Get-Content -Raw -Path $TokenFile).Trim()
} else {
    $secure = Read-Host -AsSecureString "Relay token for $Computer (from Settings > Relays)"
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
}
if (-not $token.StartsWith('hdr_')) { throw "That doesn't look like a relay token (hdr_...)." }
$file = Join-Path $TokenPath "$($account.Name).token"
if ($PSCmdlet.ShouldProcess($file, "write a token readable only by $who")) {
    # Create it empty and locked down first, then write the token into it.
    New-Item -ItemType File -Path $file -Force | Out-Null
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'Allow')))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($who, 'Read', 'Allow')))
    Set-Acl -Path $file -AclObject $acl
    Set-Content -Path $file -Value $token -NoNewline -Encoding ascii
    Write-Output "Wrote $file (readable by $who and Administrators only)."
}
