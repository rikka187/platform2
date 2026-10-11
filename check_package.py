"""交付包验收：解压到临时目录，检查文件齐全、无垃圾文件，并跑通自检与关键测试。"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ZIP = Path(r'D:\platform2\platform_v1_deploy.zip')
PY = sys.executable

with zipfile.ZipFile(ZIP) as zf:
    names = zf.namelist()

print(f'包体积 {ZIP.stat().st_size / 1024:.1f} KB｜文件 {len(names)} 个')

# 1) 不应该出现的文件
junk = [n for n in names if '__pycache__' in n or n.endswith(('.pyc', '.pyo', '.db', '.db-wal', '.db-shm'))
        or '/.venv/' in n or n.endswith('.zip')]
print('垃圾文件：', junk or '无')

# 2) 本轮新增/修改的关键文件是否在包里
must = [
    'platform_v1/simulation/vrp_optimizer.py',
    'platform_v1/simulation/joint_plan.py',
    'platform_v1/simulation/lifecycle.py',
    'platform_v1/simulation/planning.py',
    'platform_v1/simulation/costing.py',
    'platform_v1/simulation/docks.py',
    'platform_v1/simulation/charging.py',
    'platform_v1/simulation/scoring.py',
    'platform_v1/app/api_plan.py',
    'platform_v1/app/api_monitor.py',
    'platform_v1/app/db.py',
    'platform_v1/app/static/plan.html',
    'platform_v1/app/static/index.html',
    'platform_v1/app/static/shell.js',
    'platform_v1/app/static/monitor.html',
    'platform_v1/app/static/scenarios.html',
    'platform_v1/data/case2/customer_profile_paper.csv',
    'platform_v1/tests/test_vrp_paper.py',
    'platform_v1/tools/build_paper_profile.py',
    'platform_v1/tools/calibrate_s0.py',
    'platform_v1/tools/calibrate_ch3.py',
    'platform_v1/tools/verify_server.py',
    'platform_v1/deploy/check.sh',
    'platform_v1/docs/平台使用手册.md',
]
missing = [item for item in must if item not in names]
print('缺失关键文件：', missing or '无')

# 3) 页面里的新控件是否已打包（说明 UI 改动生效）
with zipfile.ZipFile(ZIP) as zf:
    plan_html = zf.read('platform_v1/app/static/plan.html').decode('utf-8')
    shell_js = zf.read('platform_v1/app/static/shell.js').decode('utf-8')
checks = {
    'plan.html 求解器含论文口径 ALNS': 'value="alns"' in plan_html,
    'plan.html 算例口径选择': 'id="pProfile"' in plan_html,
    'plan.html 发车策略选择': 'id="pPolicy"' in plan_html,
    'plan.html 车队含 C4': 'value="c4"' in plan_html,
    'plan.html 品类含全部': 'value="全部"' in plan_html,
    'plan.html 日总配送成本头条': '日总配送成本' in plan_html,
    'shell.js 顶栏时钟': 'shellClock' in shell_js,
}
for label, ok in checks.items():
    print(f'   {"PASS" if ok else "FAIL"}  {label}')

# 4) 解包到临时目录，独立跑自检 + 关键测试
work = Path(tempfile.mkdtemp(prefix='pkgcheck_'))
with zipfile.ZipFile(ZIP) as zf:
    zf.extractall(work)
app_dir = work / 'platform_v1'
print(f'\n解包目录：{app_dir}')

selfcheck = subprocess.run([PY, 'server.py', '--check-only'], cwd=app_dir,
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
line = [l for l in (selfcheck.stdout or '').splitlines() if '数据库：' in l or '自检结果' in l]
print('自检：')
for item in line:
    print('   ' + item.strip())

tests = subprocess.run([PY, '-m', 'unittest', 'tests.test_vrp_paper', 'tests.test_planning'],
                       cwd=app_dir, capture_output=True, text=True, encoding='utf-8', errors='replace')
tail = (tests.stderr or '').strip().splitlines()[-3:]
print('关键测试（论文口径求解器 + 路径预处理）：')
for item in tail:
    print('   ' + item.strip())

db_created = (app_dir / 'data' / 'platform.db').exists()
print(f'首次启动自动建库：{"是" if db_created else "否"}')
shutil.rmtree(work, ignore_errors=True)
print('\n验收完成。')
