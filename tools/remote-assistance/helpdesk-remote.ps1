<#
  Service desk launcher for helpdesk-remote: links (docs/remote-assistance.md).

  The service desk's "Remote Assistance" button opens
    helpdesk-remote:offerra?host=<computer>&end=<service desk>/api/remote-sessions/<id>/end&token=<one-time>
  This script offers Remote Assistance to that computer (msra /offerra), waits until Remote
  Assistance closes, then tells the service desk the session ended so the ticket shows how long.

  It only acts on links for the service desk set at install time (ServiceDeskUrl), and only
  on plain host names, so a link from anywhere else can't make it do anything else.
#>
param([Parameter(Mandatory = $true)][string]$Uri)
$ErrorActionPreference = 'Stop'

function Fail($message) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($message, 'Layer 8: Remote Assistance', 'OK', 'Warning') | Out-Null
    exit 1
}

$base = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\HelpdeskRemote' -Name ServiceDeskUrl -ErrorAction SilentlyContinue).ServiceDeskUrl
if (-not $base) { Fail 'The service desk launcher is not set up on this PC (ServiceDeskUrl is missing).' }
$base = $base.TrimEnd('/')

if ($Uri -notmatch '^helpdesk-remote:offerra\?(.+)$') { Fail "Not a service desk link: $Uri" }
$query = @{}
foreach ($pair in $Matches[1].Split('&')) {
    $k, $v = $pair.Split('=', 2)
    $query[[Uri]::UnescapeDataString($k)] = [Uri]::UnescapeDataString([string]$v)
}
$target = $query['host']
if ($target -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$') { Fail "Not a computer name: $target" }
$end = $query['end']
if ($end -and -not $end.StartsWith("$base/api/remote-sessions/", [StringComparison]::OrdinalIgnoreCase)) {
    Fail "This link is for another service desk ($end), not $base."
}

$msra = Join-Path $env:SystemRoot 'System32\msra.exe'
Start-Process -FilePath $msra -ArgumentList '/offerra', $target -Wait

if ($end -and $query['token']) {
    try {
        Invoke-RestMethod -Method Post -Uri ($end + '?token=' + [Uri]::EscapeDataString($query['token'])) -UseBasicParsing | Out-Null
    } catch {
        # The session still happened; the technician can end it on the ticket.
    }
}
