"""终检：① 是否还有案例/表格/论文/选题字样（排除"可复现"这类正常用语）；② 页面脚本语法。"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(r'D:\platform2\platform_v1')
PATTERN = re.compile(r'选题|案例|表2-\d|论文|第[一二三四五]章|4\.\d\.\d|F1\+F2')

hits = []
for path in ROOT.rglob('*'):
    if not path.is_file() or path.suffix not in ('.html', '.js', '.css', '.py', '.sh', '.md'):
        continue
    if any(part in ('__pycache__', '.venv') for part in path.parts):
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    for number, line in enumerate(text.splitlines(), 1):
        if PATTERN.search(line):
            hits.append((path.relative_to(ROOT).as_posix(), number, line.strip()[:96]))

print(f'① 案例/表格/论文/选题字样：{len(hits)} 处')
for rel, number, line in hits[:20]:
    print(f'   {rel}:{number}: {line}')

static = ROOT / 'app' / 'static'
out = pathlib.Path(r'D:\platform2\.pagejs')
out.mkdir(exist_ok=True)
bad = 0
count = 0
for name in ['index.html', 'plan.html', 'scenarios.html', 'monitor.html', 'customers.html',
             'vehicles.html', 'params.html', 'users.html', 'login.html', 'shell.js', 'app.js']:
    text = (static / name).read_text(encoding='utf-8')
    blocks = [text] if name.endswith('.js') else re.findall(
        r'<script(?![^>]*src=)[^>]*>(.*?)</script>', text, re.S)
    for index, block in enumerate(blocks):
        target = out / (name.replace('.', '_') + '.' + str(index) + '.js')
        target.write_text(block, encoding='utf-8')
        count += 1
        result = subprocess.run(['C:/Program Files/nodejs/node.exe', '--check', str(target)],
                                capture_output=True, text=True)
        if result.returncode:
            bad += 1
            print('   语法错误', name, (result.stderr or '').strip().splitlines()[:1])
print(f'② 页面脚本 {count} 个，语法错误 {bad} 个')

# 关键中文标签是否仍在（防止替换误伤）
labels = (ROOT / 'app' / 'labels.py').read_text(encoding='utf-8')
plan = (static / 'plan.html').read_text(encoding='utf-8')
checks = {
    '参数中文标签仍在': "'avg_speed_kmh': ('平均行驶速度'" in labels,
    '车队中文仍在': "'baseline': '现状 10 辆" in labels,
    '求解器中文仍在': "'alns': '智能路径优化'" in labels,
    '方案页求解器中文': 'solver_label' in plan,
    '方案页数据口径下拉': '数据口径' in plan,
}
for text, ok in checks.items():
    print(f'   {"PASS" if ok else "FAIL"}  {text}')
