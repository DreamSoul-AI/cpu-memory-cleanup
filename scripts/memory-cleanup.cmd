@echo off
setlocal
if not exist "%~dp0..\target\release\cpu-memory-cleanup.exe" (
  echo Rust executable is missing. Run scripts\build.ps1 first.
  pause
  exit /b 1
)
start "" powershell.exe -NoProfile -WindowStyle Hidden -File "%~dp0run-web.ps1" %*
exit /b %ERRORLEVEL%
