# Developer verification. Requires PowerShell 7; does not perform maintenance.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ArchivePath,
    [Parameter(Mandatory=$true)][string]$ReleaseMetadataPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Choose a new test output directory.' }
$taskOut = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $taskOut | Out-Null
$taskScript = Join-Path $PSScriptRoot 'install-runtime.ps1'
$taskShell = Join-Path $PSHOME 'pwsh.exe'
$taskArchive = (Resolve-Path -LiteralPath $ArchivePath).Path
$taskMetadataFile = (Resolve-Path -LiteralPath $ReleaseMetadataPath).Path
$taskMetadata = Get-Content -LiteralPath $taskMetadataFile -Raw -Encoding utf8 | ConvertFrom-Json
. (Join-Path $PSScriptRoot 'release.ps1')
$taskRelease = Resolve-AgentRelease -Metadata $taskMetadata
$taskResults = [Collections.Generic.List[object]]::new()
# Isolated parser inputs test future tags; no future executable is created or run.
$taskFuture = $taskMetadata | ConvertTo-Json -Depth 20 | ConvertFrom-Json
$taskFuture.tag_name = 'v9.8.7'
$taskFuture.html_url = 'https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v9.8.7'
$taskFuture.assets = @([pscustomobject]@{name='cpu-memory-cleanup-9.8.7-windows-x64.zip';state='uploaded';
    digest=('sha256:' + ('a' * 64));id=123;
    browser_download_url='https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/download/v9.8.7/cpu-memory-cleanup-9.8.7-windows-x64.zip'})
if ((Resolve-AgentRelease -Metadata $taskFuture).version -ne '9.8.7') { throw 'Version resolution is pinned.' }
$taskResults.Add([ordered]@{case='future-version-selection';passed=$true})
$taskFuture.prerelease = $true
$taskRejected = $false
try { Resolve-AgentRelease -Metadata $taskFuture | Out-Null } catch { $taskRejected = $true }
if (-not $taskRejected) { throw 'Prerelease was accepted.' }
$taskResults.Add([ordered]@{case='stable-release-only';passed=$true})
function Invoke-Setup([string]$Name, [string]$Root, [string]$Archive, [bool]$Success, [string]$SelectedVersion = 'latest') {
    $taskStart = [Diagnostics.ProcessStartInfo]::new()
    $taskStart.FileName = $taskShell
    $taskStart.UseShellExecute = $false
    $taskStart.CreateNoWindow = $true
    $taskStart.RedirectStandardOutput = $true
    $taskStart.RedirectStandardError = $true
    foreach ($taskArgument in @('-NoProfile', '-NonInteractive', '-File', $taskScript,
        '-RuntimeRoot', $Root, '-ArchivePath', $Archive, '-ReleaseMetadataPath', $taskMetadataFile, '-Version', $SelectedVersion)) { $taskStart.ArgumentList.Add($taskArgument) }
    $taskProcess = [Diagnostics.Process]::Start($taskStart)
    $taskStdout = $taskProcess.StandardOutput.ReadToEnd()
    $taskStderr = $taskProcess.StandardError.ReadToEnd()
    $taskProcess.WaitForExit()
    $taskStdout | Set-Content (Join-Path $taskOut ($Name + '.stdout.json')) -Encoding utf8
    $taskStderr | Set-Content (Join-Path $taskOut ($Name + '.stderr.txt')) -Encoding utf8
    $taskCode = $taskProcess.ExitCode
    $taskProcess.Dispose()
    if (($taskCode -eq 0) -ne $Success) { throw "$Name unexpected exit $taskCode : $taskStderr" }
    $taskResults.Add([ordered]@{case=$Name;exit_code=$taskCode;passed=$true})
    if ($Success) { return ($taskStdout | ConvertFrom-Json) }
}
$taskFreshRoot = Join-Path $taskOut 'fresh runtime'
$taskFresh = Invoke-Setup 'fresh' $taskFreshRoot $taskArchive $true
if ($taskFresh.version -ne $taskRelease.version -or $taskFresh.reused -or $taskFresh.maintenance_dispatched -or $taskFresh.sampling_performed) { throw 'Unexpected initialization side effect.' }
$taskBefore = (Get-FileHash -LiteralPath $taskFresh.executable).Hash
$taskReuse = Invoke-Setup 'explicit-version-reuse' $taskFreshRoot (Join-Path $taskOut 'missing.zip') $true $taskRelease.tag
if (-not $taskReuse.reused -or (Get-FileHash -LiteralPath $taskFresh.executable).Hash -ne $taskBefore) { throw 'Reuse replaced the executable.' }
$taskBadArchive = Join-Path $taskOut 'corrupt.zip'
[IO.File]::WriteAllText($taskBadArchive, 'not the official release')
$taskBadRoot = Join-Path $taskOut 'corrupt runtime'
Invoke-Setup 'corrupt-archive' $taskBadRoot $taskBadArchive $false
if (@(Get-ChildItem -LiteralPath $taskBadRoot -Directory).Count -ne 0) { throw 'Corrupt archive produced an installation.' }
# Deliberately alter only the test-owned executable; setup must reject it before invocation.
$taskTampered = [IO.File]::ReadAllBytes($taskFresh.executable)
$taskTampered[0] = $taskTampered[0] -bxor 1
[IO.File]::WriteAllBytes($taskFresh.executable, $taskTampered)
$taskTamperedHash = (Get-FileHash -LiteralPath $taskFresh.executable).Hash
Invoke-Setup 'tampered-runtime' $taskFreshRoot $taskArchive $false
if ((Get-FileHash -LiteralPath $taskFresh.executable).Hash -ne $taskTamperedHash) { throw 'Setup silently replaced a changed installation.' }
$taskGood = Invoke-Setup 'readonly-install' (Join-Path $taskOut 'readonly runtime') $taskArchive $true
$taskStatus = & $taskGood.executable --tool memory_status | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $taskStatus.completion -ne 'success' -or
    $taskStatus.samples[0].metrics.physical.total_bytes -le 0 -or $taskStatus.action) {
    throw 'Readonly task did not return an actual memory snapshot.'
}
$taskStatus | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $taskOut 'readonly-status.json') -Encoding utf8
[ordered]@{cases=@($taskResults.ToArray());real_readonly_passed=$true;maintenance_dispatched=$false;
    initializer_host=('PowerShell '+$PSVersionTable.PSVersion.ToString());version=$taskGood.version;build_id=$taskGood.build_id} |
    ConvertTo-Json -Depth 5 | Set-Content (Join-Path $taskOut 'summary.json') -Encoding utf8
Get-Content -LiteralPath (Join-Path $taskOut 'summary.json')
