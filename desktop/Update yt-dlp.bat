@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade "yt-dlp[default]"
pause
