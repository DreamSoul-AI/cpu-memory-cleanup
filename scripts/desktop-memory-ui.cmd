@echo off
setlocal
cd /d "%~dp0.."
if defined PYTHONPATH (
  set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%CD%\src"
)
python -m cpu_memory_cleanup --gui %*
exit /b %ERRORLEVEL%
