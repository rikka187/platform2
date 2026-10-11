"""抽取两份 PDF 的文本，便于检索表 2-4、案例 2 等信息。"""
import re
import sys
from pathlib import Path

from pypdf import PdfReader

OUT = Path(r'D:\platform2\.pdftext')
OUT.mkdir(exist_ok=True)

JOBS = [
    (r'C:\Users\rikka\Desktop\物流设计大赛\选题.pdf', 'xuanti'),
    (r'C:\Users\rikka\Desktop\物流设计大赛\my work\物流设计大赛论文.pdf', 'lunwen'),
]

for src, tag in JOBS:
    path = Path(src)
    if not path.is_file():
        print(f'!! 找不到 {path}')
        continue
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ''
        except Exception as exc:
            text = f'[抽取失败: {exc}]'
        pages.append(f'\n<<<PAGE {i}>>>\n{text}')
    blob = ''.join(pages)
    target = OUT / f'{tag}.txt'
    target.write_text(blob, encoding='utf-8')
    print(f'{tag}: {len(reader.pages)} 页, {len(blob)} 字符 -> {target}')
    for kw in ('表2-4', '表 2-4', '案例2', '案例 2', '经纬', '服务时间', '可接受'):
        hits = [m.start() for m in re.finditer(re.escape(kw), blob)]
        if hits:
            print(f'   {kw}: {len(hits)} 次')
