# Initialize a selected official CLI release. No sampling, maintenance, GUI or startup task.
[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [string]$Version = 'latest',
    [string]$ArchivePath,
    [string]$ReleaseMetadataPath
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release.ps1')
if ($env:OS -ne 'Windows_NT') { throw 'This Skill runtime supports Windows x64 only.' }
$taskArchitecture = $env:PROCESSOR_ARCHITEW6432
if (-not $taskArchitecture) { $taskArchitecture = $env:PROCESSOR_ARCHITECTURE }
if ($taskArchitecture -ne 'AMD64') { throw 'This runtime requires Windows x64 (AMD64).' }
if ($Version -ne 'latest' -and $Version -notmatch '^v?\d+\.\d+\.\d+(?:\+[0-9A-Za-z.-]+)?$') {
    throw 'Version must be latest or a stable release version.'
}
if (-not $RuntimeRoot) {
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; specify an absolute RuntimeRoot.' }
    $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'DreamSoul/CPU Memory Cleanup/agent-runtime'
}
if (-not [IO.Path]::IsPathRooted($RuntimeRoot)) { throw 'RuntimeRoot must be an absolute local directory.' }
$taskRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
if ($taskRoot -eq [IO.Path]::GetPathRoot($taskRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)) {
    throw 'Do not use a drive root as RuntimeRoot.'
}
$taskApi = 'https://api.github.com/repos/DreamSoul-AI/cpu-memory-cleanup/releases/'
$taskHeaders = @{Accept='application/vnd.github+json'; 'User-Agent'='cpu-memory-cleanup-agent-setup'; 'X-GitHub-Api-Version'='2022-11-28'}
$taskTls = [Net.ServicePointManager]::SecurityProtocol
try {
    [Net.ServicePointManager]::SecurityProtocol = $taskTls -bor [Net.SecurityProtocolType]::Tls12
    if ($ReleaseMetadataPath) {
        $taskMetadata = Get-Content -LiteralPath $ReleaseMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        $taskEndpoint = $taskApi + 'latest'
        if ($Version -ne 'latest') { $taskEndpoint = $taskApi + 'tags/' + [uri]::EscapeDataString('v' + $Version.TrimStart('v')) }
        $taskMetadata = Invoke-RestMethod -Uri $taskEndpoint -Headers $taskHeaders -TimeoutSec 30
    }
} finally { [Net.ServicePointManager]::SecurityProtocol = $taskTls }
$taskRelease = Resolve-AgentRelease -Metadata $taskMetadata -Version $Version
$taskFileNames = @('cpu-memory-cleanup.exe', 'LICENSE', 'THIRD_PARTY_NOTICES.txt')
function Read-Catalog([string]$Directory, [string]$Build = '') {
    # The CLI writes UTF-8 JSON, independently of the console code page.
    $taskStart = New-Object Diagnostics.ProcessStartInfo
    $taskStart.FileName = Join-Path $Directory 'cpu-memory-cleanup.exe'
    $taskStart.Arguments = '--tool-info'
    $taskStart.UseShellExecute = $false
    $taskStart.CreateNoWindow = $true
    $taskStart.RedirectStandardOutput = $true
    $taskStart.RedirectStandardError = $true
    $taskStart.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $taskStart.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $taskProcess = [Diagnostics.Process]::Start($taskStart)
    try {
        $taskCatalog = $taskProcess.StandardOutput.ReadToEnd()
        $taskError = $taskProcess.StandardError.ReadToEnd()
        $taskProcess.WaitForExit()
        if ($taskProcess.ExitCode -ne 0) { throw "Static CLI catalog failed: $taskError" }
    } finally { $taskProcess.Dispose() }
    $taskInfo = $taskCatalog | ConvertFrom-Json
    if ($taskInfo.version -ne $taskRelease.version -or $taskInfo.implementation -ne 'rust' -or
        -not $taskInfo.build_id -or -not @($taskInfo.tools).Count -or ($Build -and $taskInfo.build_id -ne $Build)) {
        throw 'Runtime identity or tool catalog does not match the selected release.'
    }
    return $taskInfo
}
function Test-Runtime([string]$Directory) {
    $taskReceipt = Get-Content -LiteralPath (Join-Path $Directory 'install-receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskReceipt.schema -ne 1 -or $taskReceipt.tag -ne $taskRelease.tag -or
        $taskReceipt.archive_sha256 -ne $taskRelease.sha256 -or -not $taskReceipt.build_id) {
        throw 'Installation receipt does not match the selected release; preserving it.'
    }
    foreach ($taskName in $taskFileNames) {
        $taskFile = Join-Path $Directory $taskName
        $taskHash = [string]$taskReceipt.files.$taskName
        if ($taskHash -notmatch '^[a-fA-F0-9]{64}$' -or -not (Test-Path -LiteralPath $taskFile -PathType Leaf) -or
            (Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash -ne $taskHash) {
            throw "Runtime file is missing or changed: $taskName. Preserving existing files; nothing was executed."
        }
    }
    return (Read-Catalog $Directory $taskReceipt.build_id)
}
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
$taskLock = [IO.File]::Open((Join-Path $taskRoot '.install.lock'), [IO.FileMode]::OpenOrCreate,
    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$taskStage = $null
# Keep releases and replaced assets separate; never overwrite an existing executable.
$taskInstalled = Join-Path $taskRoot ($taskRelease.tag + '-' + $taskRelease.sha256.Substring(0,12))
$taskReused = $false
try {
    if (Test-Path -LiteralPath $taskInstalled) {
        $taskInfo = Test-Runtime $taskInstalled
        $taskReused = $true
    } else {
        $taskStage = Join-Path $taskRoot ('stage-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $taskStage | Out-Null
        $taskArchive = $ArchivePath
        if (-not $taskArchive) {
            $taskArchive = Join-Path $taskStage 'release.zip'
            $taskTls = [Net.ServicePointManager]::SecurityProtocol
            try {
                [Net.ServicePointManager]::SecurityProtocol = $taskTls -bor [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $taskRelease.url -OutFile $taskArchive -UseBasicParsing -TimeoutSec 60
            } finally { [Net.ServicePointManager]::SecurityProtocol = $taskTls }
        }
        if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash -ne $taskRelease.sha256) {
            throw 'Release ZIP SHA256 mismatch. Nothing was executed or installed.'
        }
        $taskExtracted = Join-Path $taskStage 'extracted'
        Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskExtracted
        $taskCandidate = Join-Path $taskStage 'runtime'
        New-Item -ItemType Directory -Path $taskCandidate | Out-Null
        Copy-Item -LiteralPath (Join-Path $taskExtracted 'target/release/cpu-memory-cleanup.exe') -Destination $taskCandidate
        Copy-Item -LiteralPath (Join-Path $taskExtracted 'LICENSE'), (Join-Path $taskExtracted 'THIRD_PARTY_NOTICES.txt') -Destination $taskCandidate
        $taskHashes = [ordered]@{}
        foreach ($taskName in $taskFileNames) {
            $taskHashes[$taskName] = (Get-FileHash -LiteralPath (Join-Path $taskCandidate $taskName) -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $taskInfo = Read-Catalog $taskCandidate
        [ordered]@{
            schema=1; version=$taskRelease.version; tag=$taskRelease.tag; build_id=$taskInfo.build_id
            release=$taskRelease.release; asset_name=$taskRelease.asset_name; asset_id=$taskRelease.asset_id
            archive_sha256=$taskRelease.sha256; files=$taskHashes
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskCandidate 'install-receipt.json') -Encoding UTF8
        Move-Item -LiteralPath $taskCandidate -Destination $taskInstalled
    }
    [ordered]@{
        requested_version=$Version; version=$taskRelease.version; tag=$taskRelease.tag; build_id=$taskInfo.build_id
        executable=(Join-Path $taskInstalled 'cpu-memory-cleanup.exe')
        reused=$taskReused; release=$taskRelease.release; archive_sha256=$taskRelease.sha256
        tools=@($taskInfo.tools | ForEach-Object { $_.name }); platform_capabilities=$taskInfo.platform_capabilities
        result_persistence=$taskInfo.result_persistence
        sampling_performed=$false; maintenance_dispatched=$false
    } | ConvertTo-Json -Depth 4
} finally {
    $taskLock.Dispose()
    if ($taskStage -and (Test-Path -LiteralPath $taskStage)) {
        $taskFullStage = [IO.Path]::GetFullPath($taskStage)
        if (-not $taskFullStage.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path $taskFullStage -Leaf) -notmatch '^stage-[a-f0-9]{32}$') {
            throw 'Unexpected staging path; preserving it.'
        }
        Remove-Item -LiteralPath $taskFullStage -Recurse -Force
    }
}
