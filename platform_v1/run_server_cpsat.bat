@echo off
chcp 65001 >nul
python -c "from ortools.sat.python import cp_model; print('CP-SAT OK')" 2>nul
if errorlevel 1 (
  echo CP-SAT ÉĞÎ´°²×°£¬ÇëÏÈË«»÷ install_cpsat.bat¡£
  pause
  exit /b 1
)
python server.py --host 0.0.0.0 --port 8787
pause
