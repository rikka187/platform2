"""重新打包并校验包内关键内容（logo、侧栏修复、中文文案、企业化措辞）。"""
import os
import zipfile
from pathlib import Path

SRC = Path(r'D:\platform2\platform_v1')
OUT = Path(r'D:\platform2\platform_v1_deploy.zip')
EX_DIRS = {'.venv', '__pycache__', '.vendor', '.git', '.idea', '.vscode', '.pytest_cache'}
EX_SUFFIX = {'.pyc', '.pyo', '.zip', '.db', '.db-wal', '.db-shm'}

count = 0
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in EX_DIRS]
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() in EX_SUFFIX:
                continue
            zf.write(path, path.relative_to(SRC.parent).as_posix())
            count += 1

with zipfile.ZipFile(OUT) as zf:
    names = set(zf.namelist())

    def read(rel):
        return zf.read('platform_v1/' + rel).decode('utf-8', 'replace')

    admin_css = read('app/static/admin.css')
    shell_js = read('app/static/shell.js')
    index_html = read('app/static/index.html')
    plan_html = read('app/static/plan.html')
    labels = read('app/labels.py')

    checks = {
        '侧栏宽度 256px': 'grid-template-columns:256px 1fr' in admin_css,
        '品牌 logo 42px': 'mark-logo{width:42px' in admin_css,
        '副标题强制单行': 'white-space:nowrap' in admin_css and 'text-overflow:ellipsis' in admin_css,
        '侧栏已用 logo 图片': 'mark-logo' in shell_js and '>YL<' not in shell_js,
        '顶栏时钟': 'shellClock' in shell_js,
        'logo.png 在包内': 'platform_v1/app/static/logo.png' in names,
        'favicon.png 在包内': 'platform_v1/app/static/favicon.png' in names,
        '页面 favicon 指向 PNG': 'favicon.png' in read('app/static/login.html'),
        '主页无案例字样': '选题案例' not in index_html and '表2-' not in index_html,
        '方案页无论文/表格字样': ('论文' not in plan_html and '表2-' not in plan_html),
        '参数中文标签': "'avg_speed_kmh': ('平均行驶速度'" in labels,
        '求解器中文（智能路径优化）': "'alns': '智能路径优化'" in labels,
    }
    junk = [n for n in names if '__pycache__' in n or n.endswith(('.pyc', '.db')) or '/.venv/' in n]

print(f'包体积 {OUT.stat().st_size / 1024:.1f} KB｜文件 {count} 个｜垃圾文件 {junk or "无"}')
for label, ok in checks.items():
    print(f'  {"PASS" if ok else "FAIL"}  {label}')
