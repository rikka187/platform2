"""盘点界面上与"案例 / 表2-x / 论文 / 第N章 / 选题"相关的文字。"""
import pathlib
import re

ROOT = pathlib.Path(r'D:\platform2\platform_v1')
TARGETS = ['.']
PATTERN = re.compile(r'选题|案例|表2-\d|论文|第[一二三四五]章|4\.\d\.\d|F1\+F2|复现')

hits = 0
for rel in TARGETS:
    path = ROOT / rel
    if path.is_dir():
        files = [p for p in path.rglob('*') if p.is_file() and p.suffix in ('.html', '.js', '.css', '.py')]
    elif path.is_file():
        files = [path]
    else:
        continue
    for file in files:
        if '__pycache__' in file.parts:
            continue
        try:
            text = file.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits += 1
                print(f"{file.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:118]}")
print(f'\n共 {hits} 处')
