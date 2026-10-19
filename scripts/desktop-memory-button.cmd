@echo off
setlocal
cd /d "%~dp0.."
if /I "%~1"=="report" goto report

echo.
echo Desktop memory button. Working-set release only. Standby purge is not called.
echo Runs immediately. No confirmation and no per-item selection.
echo Does not end processes, stop services, or change system settings.
echo.
python -m cpu_memory_cleanup --release --release-what working-set
set RC=%ERRORLEVEL%
echo.
echo Before/after available and commit are the lines above. A higher available number is not a proven benefit.
echo Measured evidence, daily desktop load, AA,AB,AA,AB, 8 windows x 30 samples x 10 s:
echo   working-set empty  2026-10-20 02:04-02:43 Asia/Shanghai  cosmetic-or-harmful
echo   Commit did not fall. Hard faults rose above that run's A/A ceiling, and a refault cost follows.
echo   Standby purge is excluded: standby-only A/B showed available only +0.24/+0.30 GiB with about 3x the refault ceiling.
echo This button does not end processes and does not change system settings, the page file, or the registry.
echo Report-only, no release: run this script with the argument report.
echo.
pause
exit /b %RC%

:report
echo.
echo Desktop memory button, report-only. This does not release memory.
echo.
python -m cpu_memory_cleanup
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
