function Get-ProjectCargo {
    $taskRoot = Split-Path $PSScriptRoot -Parent
    $taskInstalled = Get-Command cargo -ErrorAction SilentlyContinue
    if ($taskInstalled) { return $taskInstalled.Source }
    $taskNative = Join-Path $taskRoot '.local/rust/native'
    $taskCargo = Join-Path $taskNative 'bin/cargo.exe'
    if (-not (Test-Path -LiteralPath $taskCargo)) { throw '请先从 https://rust-lang.org/tools/install/ 安装 Rust 工具链。' }
    $taskLink = Join-Path $taskNative 'lib/rustlib/x86_64-pc-windows-gnu/bin/self-contained'
    $env:PATH = "$taskNative/bin;$taskLink;" + $env:PATH
    $env:RUSTC = Join-Path $taskNative 'bin/rustc.exe'
    $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = Join-Path $taskLink 'x86_64-w64-mingw32-gcc.exe'
    $env:CARGO_HOME = Join-Path $taskRoot '.local/rust/cargo-home'
    return $taskCargo
}
