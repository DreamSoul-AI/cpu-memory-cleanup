param([string]$AssemblerDirectory)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot/rust-environment.ps1"
$taskCargo = Get-ProjectCargo
if ($AssemblerDirectory) {
    $taskAssembler = (Resolve-Path -LiteralPath $AssemblerDirectory).Path
    if (-not (Test-Path -LiteralPath (Join-Path $taskAssembler 'as.exe'))) { throw 'Assembler directory must contain as.exe.' }
    $env:PATH = "$taskAssembler;" + $env:PATH
}
& $taskCargo build --release --locked --features desktop-window --bin memory-cleanup-window
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$taskMetadataText = & $taskCargo metadata --locked --format-version 1 --features desktop-window --filter-platform x86_64-pc-windows-gnu
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$taskMetadata = $taskMetadataText | ConvertFrom-Json
$taskBinding = @($taskMetadata.packages | Where-Object name -eq 'webview2-com-sys')
if ($taskBinding.Count -ne 1) { throw 'Expected one locked WebView2 binding package.' }
$taskLoader = Join-Path (Split-Path $taskBinding[0].manifest_path -Parent) 'x64/WebView2Loader.dll'
$taskBuild = Join-Path $taskMetadata.target_directory 'release'
Copy-Item -LiteralPath $taskLoader -Destination (Join-Path $taskBuild 'WebView2Loader.dll')
Get-FileHash -LiteralPath (Join-Path $taskBuild 'memory-cleanup-window.exe'),(Join-Path $taskBuild 'WebView2Loader.dll') -Algorithm SHA256
