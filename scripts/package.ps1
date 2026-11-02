param(
    [string]$BuildDirectory = 'target/release',
    [string]$OutputDirectory = 'dist'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location $taskRoot
$taskVersion = (Select-String -LiteralPath Cargo.toml -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
$taskBuild = [IO.Path]::GetFullPath($BuildDirectory)
$taskOut = [IO.Path]::GetFullPath($OutputDirectory)
$taskName = "cpu-memory-cleanup-v$taskVersion-windows-x64"
$taskStage = Join-Path $taskOut $taskName
$taskZip = Join-Path $taskOut "$taskName.zip"
if ((Test-Path -LiteralPath $taskStage) -or (Test-Path -LiteralPath $taskZip)) { throw '产物已存在，请使用新输出目录，避免覆盖。' }
foreach ($taskFile in @('cpu-memory-cleanup.exe', 'memory-cleanup.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskBuild $taskFile))) { throw "缺少构建产物：$taskFile" }
}
$taskCatalog = & (Join-Path $taskBuild 'cpu-memory-cleanup.exe') --tool-info
if ($LASTEXITCODE -ne 0) { throw '无法读取工具目录' }
$taskInfo = $taskCatalog | ConvertFrom-Json
if ($taskInfo.version -ne $taskVersion) { throw 'EXE版本与Cargo版本不一致' }
New-Item -ItemType Directory -Path (Join-Path $taskStage 'target/release') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $taskStage 'scripts') | Out-Null
foreach ($taskFile in @('cpu-memory-cleanup.exe', 'memory-cleanup.exe')) {
    Copy-Item -LiteralPath (Join-Path $taskBuild $taskFile) -Destination (Join-Path $taskStage 'target/release')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'memory-cleanup.cmd') -Destination (Join-Path $taskStage 'scripts')
Copy-Item -LiteralPath LICENSE -Destination $taskStage
@"
CPU 内存清理 $taskVersion · Windows x64

解压到普通用户可写目录，双击 scripts/memory-cleanup.cmd。
默认浏览器打开 http://127.0.0.1:14800/，显示真实内存。
点击绿色按钮才执行一次真实工作集整理。自动规则默认关闭。
关闭服务请使用网页“停止服务”，仅关闭标签页不会停止服务。
无需安装 Rust、Node 或 Python。记录保存在解压目录的 .test-results/rust-runs。

工作集整理不删除应用数据，后续访问可能重新驻留。容量变化不保证提速。
若端口被旧版本占用，先在旧网页停止服务，不结束其他程序。
定向/批次及规则完整实机验收尚未覆盖，Linux/macOS 不支持。
源码与MIT：https://github.com/DreamSoul-AI/cpu-memory-cleanup
构建标识：$($taskInfo.build_id)
"@ | Set-Content (Join-Path $taskStage '使用说明.txt') -Encoding utf8
[ordered]@{ version = $taskVersion; build_id = $taskInfo.build_id; architecture = 'windows-x64'; files = @(Get-ChildItem (Join-Path $taskStage 'target/release/*.exe') | Get-FileHash -Algorithm SHA256 | Select-Object @{n='name';e={Split-Path $_.Path -Leaf}}, Hash) } | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $taskStage 'manifest.json') -Encoding utf8
Compress-Archive -Path (Join-Path $taskStage '*') -DestinationPath $taskZip
$taskHash = (Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$taskHash  $taskName.zip" | Set-Content (Join-Path $taskOut 'SHA256SUMS.txt') -Encoding ascii
Write-Host $taskZip
