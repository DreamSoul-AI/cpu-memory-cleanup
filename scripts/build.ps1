$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. "$PSScriptRoot/rust-environment.ps1"
$taskCargo = Get-ProjectCargo
& $taskCargo build --release --locked
exit $LASTEXITCODE
