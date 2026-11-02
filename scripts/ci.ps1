param(
    [string]$OutputDirectory,
    [switch]$AllowDirty
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location $taskRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $taskRoot ('.test-results/local-ci/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
}
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $taskOutput) { throw "验证目录已存在，请选择新目录：$taskOutput" }
New-Item -ItemType Directory -Path $taskOutput | Out-Null
. "$PSScriptRoot/rust-environment.ps1"
$taskCargo = Get-ProjectCargo
$taskPreviousTarget = $env:CARGO_TARGET_DIR
$taskStages = [Collections.Generic.List[object]]::new()
$taskCode = 0
$taskStage = 'setup'
try {
    $env:CARGO_TARGET_DIR = Join-Path $taskOutput 'target'
    foreach ($taskStage in @('tests', 'build', 'package')) {
        $taskStart = Get-Date
        $taskLog = Join-Path $taskOutput "$taskStage.log"
        switch ($taskStage) {
            'tests' { & "$PSScriptRoot/run-tests.ps1" *> $taskLog }
            'build' { & "$PSScriptRoot/build.ps1" *> $taskLog }
            'package' {
                $taskArgs = @('package', '--locked', '--offline')
                if ($AllowDirty) { $taskArgs += '--allow-dirty' }
                & $taskCargo @taskArgs *> $taskLog
            }
        }
        $taskCode = $LASTEXITCODE
        $taskStages.Add([ordered]@{ stage = $taskStage; exit_code = $taskCode; elapsed_ms = ((Get-Date) - $taskStart).TotalMilliseconds; log = $taskLog })
        Write-Host "$taskStage : exit $taskCode ($taskLog)"
        if ($taskCode -ne 0) { break }
    }
    if ($taskCode -eq 0) {
        $taskExe = Join-Path $env:CARGO_TARGET_DIR 'release/cpu-memory-cleanup.exe'
        & $taskExe --tool-info > (Join-Path $taskOutput 'tools.json')
        $taskCode = $LASTEXITCODE
        if ($taskCode -ne 0) { throw '静态工具目录查询失败' }
        Get-ChildItem (Join-Path $env:CARGO_TARGET_DIR 'release/*.exe') |
            Get-FileHash -Algorithm SHA256 |
            Select-Object Path, Hash |
            ConvertTo-Json | Set-Content (Join-Path $taskOutput 'artifacts.json') -Encoding utf8
    }
} catch {
    $taskCode = 1
    $_ | Out-String | Set-Content (Join-Path $taskOutput 'error.txt') -Encoding utf8
} finally {
    $env:CARGO_TARGET_DIR = $taskPreviousTarget
    [ordered]@{ success = ($taskCode -eq 0); exit_code = $taskCode; allow_dirty = [bool]$AllowDirty; stages = @($taskStages.ToArray()) } |
        ConvertTo-Json -Depth 5 | Set-Content (Join-Path $taskOutput 'summary.json') -Encoding utf8
}
Write-Host "结果：$taskOutput/summary.json"
exit $taskCode
