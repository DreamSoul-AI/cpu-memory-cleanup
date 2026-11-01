@echo off
setlocal
if not exist "%~dp0..\target\release\memory-cleanup.exe" (
  echo Rust executable is missing. Run scripts\build.ps1 first.
  pause
  exit /b 1
)
cd /d "%~dp0.."
start "" "%~dp0..\target\release\memory-cleanup.exe" --output-dir "%~dp0..\.test-results\rust-runs" %*
exit /b %ERRORLEVEL%
