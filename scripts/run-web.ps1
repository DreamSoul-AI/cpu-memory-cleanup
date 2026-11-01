param([ValidateRange(1,65535)][int]$Port = 14800)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskExecutable = Join-Path $taskRoot 'target/release/cpu-memory-cleanup.exe'
if (-not (Test-Path -LiteralPath $taskExecutable)) { throw '尚未构建Rust程序。请先运行 scripts/build.ps1。' }
$taskOutput = Join-Path $taskRoot '.test-results/rust-runs'
$taskExisting = $null
try {
    $taskExisting = Invoke-RestMethod "http://127.0.0.1:$Port/api/session" -TimeoutSec 2
} catch {
    if ($_.Exception.Response) { throw }
}
if ($taskExisting) {
    if ($taskExisting.implementation -ne 'rust') { throw '端口被旧服务或其他程序占用，请先在旧网页停止服务。' }
    Start-Process "http://127.0.0.1:$Port/"
} else {
    Start-Process -FilePath $taskExecutable -WorkingDirectory $taskRoot -WindowStyle Hidden -ArgumentList @('--web','--port',"$Port",'--open','--output-dir',"`"$taskOutput`"")
}
