param(
    [string]$BuildDirectory = 'target/release',
    [string]$OutputDirectory = 'dist',
    [string]$WindowBuildDirectory
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
$taskAgentSetup = Join-Path $taskStage 'scripts/agent-skill'
New-Item -ItemType Directory -Path $taskAgentSetup | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'agent-skill/install-runtime.ps1'), (Join-Path $PSScriptRoot 'agent-skill/release.ps1') -Destination $taskAgentSetup
Copy-Item -LiteralPath LICENSE, THIRD_PARTY_NOTICES.txt -Destination $taskStage
if ($WindowBuildDirectory) {
    $taskWindowBuild = (Resolve-Path -LiteralPath $WindowBuildDirectory).Path
    $taskWindowInfo = & (Join-Path $taskWindowBuild 'memory-cleanup-window.exe') --tool-info | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $taskWindowInfo.build_id -ne $taskInfo.build_id) { throw 'Window and CLI builds differ.' }
    foreach ($taskFile in @('memory-cleanup-window.exe', 'WebView2Loader.dll')) {
        Copy-Item -LiteralPath (Join-Path $taskWindowBuild $taskFile) -Destination (Join-Path $taskStage 'target/release')
    }
}
@"
CPU 内存清理 $taskVersion · Windows x64

解压到普通用户可写目录，双击 scripts/memory-cleanup.cmd。
默认浏览器打开 http://127.0.0.1:14800/，显示真实内存。
点击绿色按钮才执行一次真实工作集整理。自动规则默认关闭。
关闭服务请使用网页“停止服务”，仅关闭标签页不会停止服务。
无需安装 Rust、Node 或 Python。网页结果保存在解压目录的 .test-results/rust-runs。
CLI/Agent 普通工具调用默认只返回 JSON，不生成运行记录；--no-record 明确禁止保存，--output-dir DIR 才显式保存。
Agent 初始化脚本位于 scripts/agent-skill/，产品代码不放入 Knowledge 技能包。

工作集整理不删除应用数据，后续访问可能重新驻留。容量变化不保证提速。
若端口被旧版本占用，先在旧网页停止服务，不结束其他程序。
Windows包提供真实整理。Linux/macOS为另行打包的只读版本，不提供主动整理。
若包含target/release/memory-cleanup-window.exe，可双击打开独立应用窗口（需WebView2 Runtime），端口14801。
脚本入口仍打开浏览器网页版，端口14800。三个入口共用同一Rust功能库。
首次全局整理可能等待数秒，圆环动效不代表内存实际归零。
源码与MIT：https://github.com/DreamSoul-AI/cpu-memory-cleanup
构建标识：$($taskInfo.build_id)
"@ | Set-Content (Join-Path $taskStage '使用说明.txt') -Encoding utf8
[ordered]@{ version = $taskVersion; build_id = $taskInfo.build_id; architecture = 'windows-x64'; files = @(Get-ChildItem (Join-Path $taskStage 'target/release/*.exe') | Get-FileHash -Algorithm SHA256 | Select-Object @{n='name';e={Split-Path $_.Path -Leaf}}, Hash) } | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $taskStage 'manifest.json') -Encoding utf8
Compress-Archive -Path (Join-Path $taskStage '*') -DestinationPath $taskZip
# Compress-Archive can emit a non-terminating file-lock error and leave a partial ZIP.
# Verify every staged file before publishing its checksum or reporting success.
$taskArchive = [IO.Compression.ZipFile]::OpenRead($taskZip)
try {
    foreach ($taskStagedFile in Get-ChildItem -LiteralPath $taskStage -Recurse -File) {
        $taskRelative = [IO.Path]::GetRelativePath($taskStage, $taskStagedFile.FullName).Replace('\', '/')
        $taskEntry = $taskArchive.GetEntry($taskRelative)
        if (-not $taskEntry) { throw "ZIP missing staged file: $taskRelative" }
        $taskStream = $taskEntry.Open()
        $taskDigest = [Security.Cryptography.SHA256]::Create()
        try { $taskEntryHash = [Convert]::ToHexString($taskDigest.ComputeHash($taskStream)) }
        finally { $taskStream.Dispose(); $taskDigest.Dispose() }
        if ($taskEntryHash -ne (Get-FileHash -LiteralPath $taskStagedFile.FullName -Algorithm SHA256).Hash) {
            throw "ZIP file differs from staging: $taskRelative"
        }
    }
} finally { $taskArchive.Dispose() }
$taskHash = (Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$taskHash  $taskName.zip" | Set-Content (Join-Path $taskOut 'SHA256SUMS.txt') -Encoding ascii
Write-Host $taskZip
