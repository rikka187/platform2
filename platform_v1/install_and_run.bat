@echo off
chcp 65001 >nul
echo 正在安装平台依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 默认源安装失败，尝试清华镜像...
  python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
python server.py --host 0.0.0.0 --port 8787
pause
