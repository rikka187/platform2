"""校验 logo 替换结果，并跑页面脚本语法检查。"""
import pathlib
import re
import subprocess

STATIC = pathlib.Path(r'D:\platform2\platform_v1\app\static')

print('=== 资源文件 ===')
for name in ('logo.png', 'favicon.png', 'favicon.svg'):
    path = STATIC / name
    print(f'  {name}: {"存在" if path.is_file() else "缺失"}'
          + (f' {path.stat().st_size / 1024:.1f} KB' if path.is_file() else ''))

pages = sorted(STATIC.glob('*.html'))
print('=== 页面引用 ===')
for page in pages:
    text = page.read_text(encoding='utf-8')
    icon = re.search(r'<link rel="icon"[^>]*>', text)
    print(f"  {page.name:18s} {(icon.group(0) if icon else '（无 favicon）')[:70]}")

print('=== 残留检查 ===')
leftovers = []
for path in list(STATIC.glob('*.html')) + list(STATIC.glob('*.js')) + list(STATIC.glob('*.css')):
    text = path.read_text(encoding='utf-8')
    if re.search(r'>\s*YL\s*<', text) or 'class="mark">YL' in text:
        leftovers.append(path.name)
print('  仍含 YL 文字标：', leftovers or '无')
for path in (STATIC / 'shell.js', STATIC / 'login.html'):
    text = path.read_text(encoding='utf-8')
    print(f'  {path.name}: mark-logo × {text.count("mark-logo")}｜logo.png × {text.count("/logo.png")}')

print('=== 页面脚本语法 ===')
out = pathlib.Path(r'D:\platform2\.pagejs')
out.mkdir(exist_ok=True)
bad = 0
count = 0
for name in ['index.html', 'plan.html', 'scenarios.html', 'monitor.html', 'customers.html',
             'vehicles.html', 'params.html', 'users.html', 'login.html', 'shell.js', 'app.js']:
    text = (STATIC / name).read_text(encoding='utf-8')
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
            print('   语法错误', name)
print(f'  {count} 个脚本，错误 {bad}')
