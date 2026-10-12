"""查看侧栏品牌区的 CSS 与标记，定位换行原因。"""
import pathlib
import re

css = pathlib.Path(r'D:\platform2\platform_v1\app\static\admin.css').read_text(encoding='utf-8')
for match in re.finditer(r'[^\n}]*(?:\.shell|\.side|\.brand)[^{]*\{[^}]*\}', css):
    block = match.group(0).strip()
    if 'brand' in block or '.shell{' in block or '.side{' in block:
        print(block[:220].replace('\n', ' '))
        print('---')

shell = pathlib.Path(r'D:\platform2\platform_v1\app\static\shell.js').read_text(encoding='utf-8')
for number, line in enumerate(shell.splitlines(), 1):
    if any(key in line for key in ('brand', 'mark-logo', '银犁', '城市冷链')):
        print(f'shell.js:{number}: {line.strip()[:160]}')
