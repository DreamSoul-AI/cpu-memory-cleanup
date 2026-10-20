@echo off
setlocal
cd /d "%~dp0.."
if defined PYTHONPATH (
  set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%CD%\src"
)
if "%~1"=="" goto readonly
if /I "%~1"=="release" goto release
goto usage

:readonly
python -m cpu_memory_cleanup
exit /b %ERRORLEVEL%

:release
if "%~2"=="" goto usage
if not "%~3"=="" goto usage
python -m cpu_memory_cleanup --release --release-what working-set --output-dir "%~2"
exit /b %ERRORLEVEL%

:usage
echo Usage: desktop-memory-button.cmd [release NEW_OUTPUT_DIRECTORY]
echo Default: read-only. Explicit release uses working-set only and requires a new record directory.
exit /b 2
