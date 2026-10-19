@echo off
setlocal
cd /d "%~dp0.."
echo.
echo Desktop memory button. Read-only. This does not release memory.
echo.
echo Three A/B runs on daily desktop load (AA,AB,AA,AB; 8 windows x 30 samples x 10 s)
echo all returned cosmetic-or-harmful. A higher free number is not a benefit.
echo   combined both      2026-10-19 05:20-05:59 Asia/Shanghai
echo   standby purge      2026-10-19 06:16-06:55 Asia/Shanghai
echo   working-set empty  2026-10-20 02:04-02:43 Asia/Shanghai
echo Commit did not fall. Hard faults, pages input, or disk I/O rose above the A/A ceiling.
echo If commit is the problem, close heavy apps yourself. This button does not end processes
echo and does not change system settings, the page file, or the registry.
echo.
python -m cpu_memory_cleanup
echo.
pause
