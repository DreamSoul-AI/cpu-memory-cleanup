<#
.SYNOPSIS
  Bounded, read-only per-owner-tree memory time series (backlog item 3).

.DESCRIPTION
  Every -IntervalSeconds, for exactly -Count samples, reads Win32_Process and
  Win32_OperatingSystem through CIM and appends ONE aggregated JSON line to
  -JsonlPath. Only aggregates are written: no PIDs, no command lines, no
  paths. Command lines are inspected in memory only to classify Codex
  computer-use node children (server.mjs / cua-repl.mjs) and are discarded.
  For server.mjs, cua-repl.mjs and node_repl.exe members of the Codex tree it
  also writes age-bucket aggregates (lt10m, 10to60m, 1to3h, gt3h, unknown):
  process count and private commit per bucket, plus the oldest age per kind.

  Each beat also records a read-only commit residual block when PDH counters
  are available: process_private_gib (PDH Process(_Total) Private Bytes, else
  the CIM private_sum_gib), paged_pool_gib, nonpaged_pool_gib,
  system_driver_gib, residual_gib and residual_pct_of_commit. Formula:
  residual_gib = commit_used_gib - process_private_gib - paged_pool_gib -
  nonpaged_pool_gib - system_driver_gib. Get-Counter failures set those
  fields to null and do not abort the series.

  Read-only: it never terminates, suspends, trims, flushes or reconfigures
  anything, never elevates, and never touches the page file or services.
  It stops after -Count samples; it is not a service. If writing the log
  fails it stops.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tree-series.ps1 `
    -IntervalSeconds 60 -Count 30 -JsonlPath logs\tree-series.jsonl
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidateRange(5, 3600)][int]$IntervalSeconds,
  [Parameter(Mandatory = $true)][ValidateRange(1, 1440)][int]$Count,
  [Parameter(Mandatory = $true)][string]$JsonlPath,
  [ValidateRange(1, 50)][int]$TopOwners = 10
)
$ErrorActionPreference = 'Stop'
$parent = Split-Path -Parent ([IO.Path]::GetFullPath($JsonlPath))
if (-not (Test-Path -LiteralPath $parent)) { throw "log directory does not exist: $parent" }

$GiB = [double](1024 * 1024 * 1024)
$runId = [guid]::NewGuid().ToString()
$anchors = @('explorer.exe', 'services.exe', 'wininit.exe', 'winlogon.exe', 'svchost.exe', 'smss.exe', 'csrss.exe', 'System', 'System Idle Process')
$prevKeys = $null
$prevCodexKeys = $null
$ageBuckets = @('lt10m', '10to60m', '1to3h', 'gt3h', 'unknown')
$ageKinds = @('server_mjs', 'cua_repl', 'node_repl')
function Get-AgeBucket($minutes) {
  if ($null -eq $minutes) { return 'unknown' }
  if ($minutes -lt 10) { return 'lt10m' }
  if ($minutes -lt 60) { return '10to60m' }
  if ($minutes -lt 180) { return '1to3h' }
  return 'gt3h'
}

for ($i = 1; $i -le $Count; $i++) {
  $start = Get-Date
  $procs = Get-CimInstance -ClassName Win32_Process -Property ProcessId, ParentProcessId, Name, PrivatePageCount, WorkingSetSize, CreationDate, CommandLine
  $os = Get-CimInstance -ClassName Win32_OperatingSystem -Property FreePhysicalMemory, TotalVisibleMemorySize, TotalVirtualMemorySize, FreeVirtualMemory

  $byPid = @{}
  foreach ($p in $procs) { $byPid[[int]$p.ProcessId] = $p }

  # A parent link is only trusted when the parent was created before the child (PID reuse guard).
  function Get-ParentOf($p) {
    $pp = $byPid[[int]$p.ParentProcessId]
    if ($null -eq $pp -or [int]$pp.ProcessId -eq [int]$p.ProcessId) { return $null }
    if ($p.CreationDate -and $pp.CreationDate -and $pp.CreationDate -gt $p.CreationDate) { return $null }
    return $pp
  }

  $owners = @{}
  $codex = @{ processes = 0; private = 0.0; ws = 0.0; byName = @{}; serverMjs = 0; serverMjsPrivate = 0.0; cuaRepl = 0; cuaReplPrivate = 0.0; oldestMinutes = $null; ages = @{}; kindOldest = @{} }
  $keys = New-Object 'System.Collections.Generic.HashSet[string]'
  $codexKeys = New-Object 'System.Collections.Generic.HashSet[string]'
  $totalPrivate = 0.0

  foreach ($p in $procs) {
    $priv = [double]$p.PrivatePageCount
    $totalPrivate += $priv
    $key = '{0}|{1}' -f $p.ProcessId, $(if ($p.CreationDate) { $p.CreationDate.Ticks } else { 0 })
    [void]$keys.Add($key)

    # Owner = highest ancestor below a session/service anchor (or with no live parent).
    $owner = $p; $inCodex = ($p.Name -eq 'codex.exe'); $depth = 0
    while ($depth -lt 64) {
      $pp = Get-ParentOf $owner
      if ($null -eq $pp -or $anchors -contains $pp.Name) { break }
      if ($pp.Name -eq 'codex.exe') { $inCodex = $true }
      $owner = $pp; $depth++
    }
    $oname = if ($anchors -contains $p.Name) { $p.Name } else { $owner.Name }
    if (-not $owners.ContainsKey($oname)) { $owners[$oname] = @{ processes = 0; private = 0.0 } }
    $owners[$oname].processes++; $owners[$oname].private += $priv

    if ($inCodex) {
      [void]$codexKeys.Add($key)
      $codex.processes++; $codex.private += $priv; $codex.ws += [double]$p.WorkingSetSize
      if (-not $codex.byName.ContainsKey($p.Name)) { $codex.byName[$p.Name] = @{ processes = 0; private = 0.0 } }
      $codex.byName[$p.Name].processes++; $codex.byName[$p.Name].private += $priv
      $cl = [string]$p.CommandLine
      $kind = $null
      if ($p.Name -eq 'node.exe' -and $cl -match 'server\.mjs') { $codex.serverMjs++; $codex.serverMjsPrivate += $priv; $kind = 'server_mjs' }
      elseif ($p.Name -eq 'node.exe' -and $cl -match 'cua-repl\.mjs') { $codex.cuaRepl++; $codex.cuaReplPrivate += $priv; $kind = 'cua_repl' }
      elseif ($p.Name -eq 'node_repl.exe') { $kind = 'node_repl' }
      if ($null -ne $kind) {
        $kAge = $null
        if ($p.CreationDate) { $kAge = ($start - $p.CreationDate).TotalMinutes }
        $bk = '{0}|{1}' -f $kind, (Get-AgeBucket $kAge)
        if (-not $codex.ages.ContainsKey($bk)) { $codex.ages[$bk] = @{ processes = 0; private = 0.0 } }
        $codex.ages[$bk].processes++; $codex.ages[$bk].private += $priv
        if ($null -ne $kAge -and (-not $codex.kindOldest.ContainsKey($kind) -or $kAge -gt $codex.kindOldest[$kind])) { $codex.kindOldest[$kind] = $kAge }
      }
      if ($p.CreationDate) {
        $age = ($start - $p.CreationDate).TotalMinutes
        if ($null -eq $codex.oldestMinutes -or $age -gt $codex.oldestMinutes) { $codex.oldestMinutes = $age }
      }
    }
  }

  $born = $null; $exited = $null; $codexBorn = $null; $codexExited = $null
  if ($null -ne $prevKeys) {
    $born = @($keys | Where-Object { -not $prevKeys.Contains($_) }).Count
    $exited = @($prevKeys | Where-Object { -not $keys.Contains($_) }).Count
    $codexBorn = @($codexKeys | Where-Object { -not $prevCodexKeys.Contains($_) }).Count
    $codexExited = @($prevCodexKeys | Where-Object { -not $codexKeys.Contains($_) }).Count
  }
  $prevKeys = $keys; $prevCodexKeys = $codexKeys

  # Read-only PDH commit residual (fail soft: null fields if counters unavailable).
  # Formula: residual = commit_used - process_private - paged_pool - nonpaged_pool - system_driver
  # (same sources as docs/validation.md Commit residual re-check).
  $commitUsedGib = [math]::Round(([double]$os.TotalVirtualMemorySize - [double]$os.FreeVirtualMemory) * 1024 / $GiB, 3)
  $processPrivateGib = [math]::Round($totalPrivate / $GiB, 3)
  $pagedPoolGib = $null
  $nonpagedPoolGib = $null
  $systemDriverGib = $null
  $residualGib = $null
  $residualPct = $null
  try {
    $counterPaths = @(
      '\Memory\Pool Paged Bytes',
      '\Memory\Pool Nonpaged Bytes',
      '\Memory\System Driver Total Bytes',
      '\Process(_Total)\Private Bytes'
    )
    $samples = (Get-Counter -Counter $counterPaths -ErrorAction Stop).CounterSamples
    $byLeaf = @{}
    foreach ($s in $samples) {
      $leaf = (($s.Path -split '\\')[-1]).ToLowerInvariant()
      $byLeaf[$leaf] = [double]$s.CookedValue
    }
    if ($byLeaf.ContainsKey('pool paged bytes')) { $pagedPoolGib = [math]::Round($byLeaf['pool paged bytes'] / $GiB, 3) }
    if ($byLeaf.ContainsKey('pool nonpaged bytes')) { $nonpagedPoolGib = [math]::Round($byLeaf['pool nonpaged bytes'] / $GiB, 3) }
    if ($byLeaf.ContainsKey('system driver total bytes')) { $systemDriverGib = [math]::Round($byLeaf['system driver total bytes'] / $GiB, 3) }
    if ($byLeaf.ContainsKey('private bytes')) { $processPrivateGib = [math]::Round($byLeaf['private bytes'] / $GiB, 3) }
  } catch {
    # Counter set missing or transient PDH error: omit residual fields, keep series going.
  }
  if ($null -ne $pagedPoolGib -and $null -ne $nonpagedPoolGib -and $null -ne $systemDriverGib) {
    $residualGib = [math]::Round($commitUsedGib - $processPrivateGib - $pagedPoolGib - $nonpagedPoolGib - $systemDriverGib, 3)
    if ($commitUsedGib -gt 0) { $residualPct = [math]::Round(100.0 * $residualGib / $commitUsedGib, 1) }
  }

  $ownerRows = $owners.GetEnumerator() | Sort-Object { $_.Value.private } -Descending | Select-Object -First $TopOwners | ForEach-Object {
    [ordered]@{ owner = $_.Key; processes = $_.Value.processes; private_gib = [math]::Round($_.Value.private / $GiB, 3) }
  }
  $codexNames = $codex.byName.GetEnumerator() | Sort-Object { $_.Value.private } -Descending | ForEach-Object {
    [ordered]@{ name = $_.Key; processes = $_.Value.processes; private_gib = [math]::Round($_.Value.private / $GiB, 3) }
  }

  $ageRows = foreach ($k in $ageKinds) {
    $row = [ordered]@{ kind = $k; oldest_minutes = $(if ($codex.kindOldest.ContainsKey($k)) { [math]::Round($codex.kindOldest[$k], 1) } else { $null }) }
    foreach ($bn in $ageBuckets) {
      $e = $codex.ages[('{0}|{1}' -f $k, $bn)]
      $row[$bn] = [ordered]@{ processes = $(if ($e) { $e.processes } else { 0 }); private_gib = $(if ($e) { [math]::Round($e.private / $GiB, 3) } else { 0.0 }) }
    }
    $row
  }

  $rec = [ordered]@{
    kind = 'tree-series'; run_id = $runId; index = $i; count = $Count; interval_s = $IntervalSeconds
    utc = $start.ToUniversalTime().ToString('o'); local = $start.ToString('yyyy-MM-dd HH:mm:ss')
    host = [ordered]@{
      total_gib = [math]::Round([double]$os.TotalVisibleMemorySize * 1024 / $GiB, 3)
      free_gib = [math]::Round([double]$os.FreePhysicalMemory * 1024 / $GiB, 3)
      commit_used_gib = $commitUsedGib
      commit_limit_gib = [math]::Round([double]$os.TotalVirtualMemorySize * 1024 / $GiB, 3)
    }
    processes = $procs.Count; private_sum_gib = [math]::Round($totalPrivate / $GiB, 3)
    commit_residual = [ordered]@{
      process_private_gib = $processPrivateGib
      paged_pool_gib = $pagedPoolGib
      nonpaged_pool_gib = $nonpagedPoolGib
      system_driver_gib = $systemDriverGib
      residual_gib = $residualGib
      residual_pct_of_commit = $residualPct
    }
    born_since_prev = $born; exited_since_prev = $exited
    codex_tree = [ordered]@{
      processes = $codex.processes; private_gib = [math]::Round($codex.private / $GiB, 3)
      ws_sum_gib_overcounted = [math]::Round($codex.ws / $GiB, 3)
      server_mjs = $codex.serverMjs; server_mjs_private_gib = [math]::Round($codex.serverMjsPrivate / $GiB, 3)
      cua_repl = $codex.cuaRepl; cua_repl_private_gib = [math]::Round($codex.cuaReplPrivate / $GiB, 3)
      born_since_prev = $codexBorn; exited_since_prev = $codexExited
      oldest_minutes = $(if ($null -ne $codex.oldestMinutes) { [math]::Round($codex.oldestMinutes, 1) } else { $null })
      by_name = @($codexNames)
      age_buckets = @($ageRows)
    }
    top_owners = @($ownerRows)
    query_seconds = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
  }
  $line = $rec | ConvertTo-Json -Compress -Depth 6
  [IO.File]::AppendAllText([IO.Path]::GetFullPath($JsonlPath), $line + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))

  if ($i -lt $Count) {
    $wait = $IntervalSeconds - ((Get-Date) - $start).TotalSeconds
    if ($wait -gt 0) { Start-Sleep -Milliseconds ([int]($wait * 1000)) }
  }
}