@echo off
chcp 65001 >nul
cd /d "%~dp0platform_v1"
start "" http://127.0.0.1:8787
python server.py --host 0.0.0.0 --port 8787
pause
