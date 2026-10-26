# SPDX-License-Identifier: AGPL-3.0-or-later
# Shared data validation; dot-sourcing this file performs no provisioning.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Assert-RelayUrl([string]$Value) {
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') { throw 'Use the HTTPS root address of Layer 8, with no credentials, query or path.' }
    return $uri.AbsoluteUri.TrimEnd('/')
}
function Assert-RelayLocalPath([string]$Value) {
    if ($Value -notmatch '^[A-Za-z]:\\' -or $Value -match '["\r\n]' -or $Value -match '\\\.\.?($|\\)') { throw 'Use an absolute local Windows path without quotes or relative segments.' }
    $full = [IO.Path]::GetFullPath($Value)
    $current = $full
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Relay paths cannot contain junctions or symbolic links.' }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    return $full
}
function Set-RelayAcl([string]$Path, [string[]]$Readers = @(), [string[]]$Writers = @()) {
    $directory = Test-Path -LiteralPath $Path -PathType Container
    if ($directory) { $acl = New-Object Security.AccessControl.DirectorySecurity } else { $acl = New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = if ($directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($entry in @(@('S-1-5-18','FullControl'),@('S-1-5-32-544','FullControl')) + @($Readers | ForEach-Object { ,@($_,'ReadAndExecute') }) + @($Writers | ForEach-Object { ,@($_,'Modify') })) {
        $sid = if ($entry[0] -match '^S-1-') { New-Object Security.Principal.SecurityIdentifier($entry[0]) } else { (New-Object Security.Principal.NTAccount($entry[0])).Translate([Security.Principal.SecurityIdentifier]) }
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,$entry[1],$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Test-RelayKit([string]$Path) {
    $manifest = Get-Content -LiteralPath (Join-Path $Path 'kit.json') -Raw | ConvertFrom-Json
    if ($manifest.format -ne 1) { throw 'Unsupported relay kit.' }
    foreach ($item in $manifest.files.PSObject.Properties) {
        if ($item.Name -notmatch '^[A-Za-z0-9_.-]+$') { throw 'Unsafe kit filename.' }
        $file = Join-Path $Path $item.Name
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file).Attributes -band [IO.FileAttributes]::ReparsePoint -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $item.Value) { throw "Kit integrity check failed: $($item.Name)" }
    }
    return $manifest
}
function Read-RelayToken([string]$Name) {
    $secure = Read-Host "Paste the one-time token for $Name (hidden)" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ($value -notmatch '^hdr_[A-Za-z0-9_-]{32,128}$') { throw 'This does not look like a relay token.' }
        return $value
    } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secure.Dispose() }
}
