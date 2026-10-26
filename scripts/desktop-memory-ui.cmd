@echo off
setlocal
cd /d "%~dp0.."
if defined PYTHONPATH (
  set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%CD%\src"
)
start "" pythonw "%~dp0desktop-memory-ui.pyw" %*
exit /b %ERRORLEVEL%
