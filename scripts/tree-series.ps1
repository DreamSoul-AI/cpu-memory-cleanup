<#
.SYNOPSIS
  Forward bounded read-only sampling to the Python application.
.DESCRIPTION
  Business sampling and record validation belong to the Python use case.
  JsonlPath remains accepted; the new file uses the versioned sample schema.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidateRange(5, 3600)][int]$IntervalSeconds,
  [Parameter(Mandatory = $true)][ValidateRange(1, 1000)][int]$Count,
  [Parameter(Mandatory = $true)][string]$JsonlPath,
  [ValidateRange(1, 50)][int]$TopOwners = 10
)
$ErrorActionPreference = 'Stop'
$outputPath = [IO.Path]::GetFullPath($JsonlPath)
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourcePath = Join-Path $projectRoot 'src'
$previousPythonPath = $env:PYTHONPATH
Push-Location -LiteralPath $projectRoot
try {
  $env:PYTHONPATH = if ([string]::IsNullOrEmpty($previousPythonPath)) {
    $sourcePath
  } else {
    $sourcePath + [IO.Path]::PathSeparator + $previousPythonPath
  }
  & python -m cpu_memory_cleanup --sample --interval $IntervalSeconds --count $Count --jsonl $outputPath --with-processes --top $TopOwners
  $resultCode = $LASTEXITCODE
} finally {
  $env:PYTHONPATH = $previousPythonPath
  Pop-Location
}
exit $resultCode
