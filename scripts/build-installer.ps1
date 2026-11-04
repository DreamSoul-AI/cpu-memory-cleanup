param(
    [Parameter(Mandatory=$true)][string]$Compiler,
    [string]$BuildDirectory = 'target/release',
    [string]$OutputDirectory = 'dist/installer',
    [string]$WindowBuildDirectory
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskCompiler = (Resolve-Path -LiteralPath $Compiler).Path
$taskBuild = (Resolve-Path -LiteralPath $BuildDirectory).Path
$taskCatalog = & (Join-Path $taskBuild 'cpu-memory-cleanup.exe') --tool-info
if ($LASTEXITCODE -ne 0) { throw 'Cannot read CLI build metadata.' }
$taskInfo = $taskCatalog | ConvertFrom-Json
$taskVersion = (Select-String -LiteralPath Cargo.toml -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
if ($taskVersion -ne $taskInfo.version) { throw 'Binary and source versions differ.' }
$taskWindowArgs = @()
if ($WindowBuildDirectory) {
    $taskWindowBuild = (Resolve-Path -LiteralPath $WindowBuildDirectory).Path
    foreach ($taskName in @('memory-cleanup-window.exe', 'WebView2Loader.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskWindowBuild $taskName))) { throw "Missing window dependency: $taskName" }
    }
    $taskWindowInfo = & (Join-Path $taskWindowBuild 'memory-cleanup-window.exe') --tool-info | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $taskWindowInfo.build_id -ne $taskInfo.build_id) { throw 'Window and CLI builds differ.' }
    $taskWindowArgs = @("/DWindowBuildDir=$taskWindowBuild")
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$taskOutput = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) "cpu-memory-cleanup-$taskVersion-windows-x64-setup.exe"
if (Test-Path -LiteralPath $taskOutput) { throw 'Output exists; choose a new directory.' }
& $taskCompiler @taskWindowArgs "/DBuildDir=$taskBuild" "/DOutputFile=$taskOutput" "/DAppVersion=$taskVersion" "$PSScriptRoot/windows/install.nsi"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
[ordered]@{version=$taskVersion;build_id=$taskInfo.build_id;desktop_window=[bool]$WindowBuildDirectory;sha256=(Get-FileHash -LiteralPath $taskOutput -Algorithm SHA256).Hash;file=$taskOutput} | ConvertTo-Json | Set-Content (Join-Path $OutputDirectory 'installer.json') -Encoding utf8
