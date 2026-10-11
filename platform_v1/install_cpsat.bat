@echo off
chcp 65001 >nul
echo 正在安装平台依赖和 OR-Tools CP-SAT...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 默认源安装失败，尝试清华镜像...
  python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
python -c "from ortools.sat.python import cp_model; print('CP-SAT OK')"
pause
