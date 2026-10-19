@echo off
cd /d "%~dp0.."
where pythonw >nul 2>&1 && start "" pythonw -m cpu_memory_cleanup.desktop_ui && exit /b 0
start "" python -m cpu_memory_cleanup.desktop_ui
