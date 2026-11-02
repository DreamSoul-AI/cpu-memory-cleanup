$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot/rust-environment.ps1"
$taskCargo = Get-ProjectCargo
& $taskCargo fmt --all -- --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskCargo clippy --all-targets --locked -- -D warnings
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Library + every integration target. The GUI-only launcher has no test cases;
# verify its compilation above instead of executing an empty GUI test harness.
& $taskCargo test --lib --test '*' --locked
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskCargo clippy --all-targets --features experimental-quota --locked -- -D warnings
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskCargo test --lib --test '*' --features experimental-quota --locked
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node --test tests/interaction/web_assets/app.test.cjs tests/interaction/web_assets/memory-display.test.cjs tests/interaction/web_assets/cleanup-flow.test.cjs
exit $LASTEXITCODE
