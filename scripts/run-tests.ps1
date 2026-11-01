$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot/rust-environment.ps1"
$taskCargo = Get-ProjectCargo
& $taskCargo fmt --all -- --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskCargo clippy --all-targets --locked -- -D warnings
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskCargo test --all-targets --locked
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test tests/interaction/web_assets/app.test.cjs
exit $LASTEXITCODE
