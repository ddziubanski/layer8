# SPDX-License-Identifier: AGPL-3.0-or-later
<#
.SYNOPSIS
  Export Track-It! (Ivanti/BMC) data into a bundle for Layer 8's import (python -m app.cli import-trackit).

.DESCRIPTION
  Run on (or near) the Track-It! server. Reads the Track-It! database read-only with the SQL Server
  client built into Windows (nothing to install) and writes one .zip:
    manifest.json             what was exported, when, and how many rows
    tables/<TABLE>.jsonl      one JSON object per row (password, salt, SID and token columns are never read)
    files/<SEQUENCE>          attachment contents (Track-It! keeps them in the database)

  Tickets, their notes, work orders and attachments, requesters, technicians, departments,
  locations, categories, priorities, statuses, solutions (knowledge base) and assets.
  The bundle holds your real data: keep it on site and delete it once imported.

.PARAMETER Server
  The SQL Server instance, e.g. TRACKITSRV\TRACKIT (Track-It!'s usual named instance).
.PARAMETER Database
  The Track-It! database name (default TrackIt).
.PARAMETER Out
  Where to write the .zip.
.PARAMETER Since
  Only tickets opened or changed since this date (with their notes, work orders and attachments).
  For a trial run or a cut-over top-up; the import adds to what an earlier run brought in.
.PARAMETER SystemNotes
  Also export Track-It!'s automatic system notes (status changes, assignments). Layer 8 keeps them
  as one internal summary per ticket. Off by default: there are many.
.PARAMETER Credential
  A SQL login (Get-Credential). Default: your Windows sign-in.
.PARAMETER TrustServerCertificate
  Accept the SQL Server's self-signed certificate.

.EXAMPLE
  .\trackit-export.ps1 -Server 'TRACKITSRV\TRACKIT' -Out C:\Temp\trackit.zip
.EXAMPLE
  .\trackit-export.ps1 -Server 'TRACKITSRV\TRACKIT' -Out C:\Temp\trial.zip -Since (Get-Date).AddDays(-10)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Server,
    [string] $Database = 'TrackIt',
    [Parameter(Mandatory)] [string] $Out,
    [datetime] $Since,
    [switch] $SystemNotes,
    [pscredential] $Credential,
    [switch] $TrustServerCertificate
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Version = 1
$Schema = '_SMDBA_'
# Columns never read: passwords, salts, SIDs, sign-in data, tokens.
$Secret = '(PWD|PASSWORD|SALT|^SID:?$|LOGININFO|TOKEN)'
$Reference = '_CUSTOMER_', '_PERSONNEL_', '_DEPART_', '_LOCATION_', '_CATEGORIES_', '_PRIORITY_', '_UDSTATUS_', '_TOPIC_', '_SOLUTION_', '_INVENTORY_', '_ASSETTYPE_'

$cs = "Server=$Server;Database=$Database;Application Name=Layer 8 Track-It! export;"
if ($Credential) { $cs += "User ID=$($Credential.UserName);Password=$($Credential.GetNetworkCredential().Password);" } else { $cs += 'Integrated Security=SSPI;' }
if ($TrustServerCertificate) { $cs += 'TrustServerCertificate=True;Encrypt=True;' }
$conn = New-Object System.Data.SqlClient.SqlConnection $cs
$conn.Open()

function Columns([string]$table) {
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = @s AND TABLE_NAME = @t ORDER BY ORDINAL_POSITION"
    [void]$cmd.Parameters.AddWithValue('@s', $Schema)
    [void]$cmd.Parameters.AddWithValue('@t', $table)
    $r = $cmd.ExecuteReader()
    $cols = @()
    while ($r.Read()) {
        $name, $type = $r.GetString(0), $r.GetString(1)
        # Binary columns (attachment contents, pictures) are written as files or left out.
        if ($name -notmatch $Secret -and $type -notin 'varbinary', 'image', 'binary', 'timestamp') { $cols += $name }
    }
    $r.Close()
    $cols
}

function Value($v) {
    if ($v -is [DBNull]) { return $null }
    if ($v -is [datetime]) { return $v.ToString('yyyy-MM-ddTHH:mm:ss') }
    if ($v -is [decimal]) { return [double]$v }
    $v
}

$stage = Join-Path ([IO.Path]::GetTempPath()) ("layer8-trackit-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $stage 'tables'), (Join-Path $stage 'files') | Out-Null
$counts = [ordered]@{}

function Export([string]$table, [string]$where = '') {
    $cols = Columns $table
    if (-not $cols) { Write-Warning "$table not found; skipped"; return }
    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = 0
    $cmd.CommandText = "SELECT " + (($cols | ForEach-Object { "[$_]" }) -join ', ') + " FROM [$Schema].[$table] $where"
    if ($Since) { [void]$cmd.Parameters.AddWithValue('@since', $Since) }
    $path = Join-Path $stage "tables/$table.jsonl"
    $w = New-Object IO.StreamWriter($path, $false, (New-Object Text.UTF8Encoding($false)))
    $r = $cmd.ExecuteReader()
    $n = 0
    while ($r.Read()) {
        $row = [ordered]@{}
        for ($i = 0; $i -lt $r.FieldCount; $i++) { $row[$r.GetName($i)] = Value $r.GetValue($i) }
        $w.WriteLine(($row | ConvertTo-Json -Compress -Depth 2))
        $n++
        if ($n % 10000 -eq 0) { Write-Host "  $table`: $n" }
    }
    $r.Close(); $w.Close()
    $counts[$table] = $n
    Write-Host "$table`: $n rows"
}

$tickets = if ($Since) { "SELECT SEQUENCE FROM [$Schema].[_TELMASTE_] WHERE [DATE OPEN] >= @since OR LASTMODIFIED >= @since" } else { $null }
$inTickets = { param($col) if ($tickets) { "[$col] IN ($tickets)" } else { '1 = 1' } }

foreach ($t in $Reference) { Export $t }
Export '_TELMASTE_' $(if ($tickets) { "WHERE SEQUENCE IN ($tickets)" } else { '' })
$noteFilter = if ($SystemNotes) { '1 = 1' } else { "ISNULL(NOTETYPE, '') <> 'System Note'" }
Export '_TELDETAI_' ("WHERE " + (& $inTickets 'PROBLEM #') + " AND $noteFilter")
Export '_WORKORD_' ("WHERE " + (& $inTickets 'HDNUM'))
Export '_WORKDET_' ("WHERE TTNUM IN (SELECT SEQUENCE FROM [$Schema].[_WORKORD_] WHERE " + (& $inTickets 'HDNUM') + ") AND $noteFilter")
$attachWhere = "WHERE [SEQ_INCIDENT:] IS NOT NULL AND " + (& $inTickets 'SEQ_INCIDENT:')
Export '_ATTACH_' $attachWhere

# Attachment contents, one file each, streamed (some are large).
$cmd = $conn.CreateCommand()
$cmd.CommandTimeout = 0
$cmd.CommandText = "SELECT SEQUENCE, FILEBLOB2 FROM [$Schema].[_ATTACH_] $attachWhere AND FILEBLOB2 IS NOT NULL"
if ($Since) { [void]$cmd.Parameters.AddWithValue('@since', $Since) }
$r = $cmd.ExecuteReader([Data.CommandBehavior]::SequentialAccess)
$files = 0; $bytes = 0L
$buf = New-Object byte[] 65536
while ($r.Read()) {
    $seq = $r.GetValue(0)
    $fs = [IO.File]::Create((Join-Path $stage "files/$seq"))
    $off = 0L
    while (($got = $r.GetBytes(1, $off, $buf, 0, $buf.Length)) -gt 0) { $fs.Write($buf, 0, $got); $off += $got }
    $fs.Close()
    $files++; $bytes += $off
}
$r.Close()
$conn.Close()
Write-Host ("attachments: {0} files, {1:N1} MB" -f $files, ($bytes / 1MB))

$manifest = [ordered]@{
    format = 'layer8-trackit-export'; version = $Version; exported_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    server = $Server; database = $Database; since = $(if ($Since) { $Since.ToString('yyyy-MM-ddTHH:mm:ss') } else { $null })
    system_notes = [bool]$SystemNotes; counts = $counts; files = $files; file_bytes = $bytes
}
[IO.File]::WriteAllText((Join-Path $stage 'manifest.json'), ($manifest | ConvertTo-Json -Depth 3), (New-Object Text.UTF8Encoding($false)))
if (Test-Path $Out) { Remove-Item $Out }
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $Out, [IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item -Recurse -Force $stage
Write-Host "Wrote $Out. It holds your Track-It! data: keep it on site, and delete it once imported."
