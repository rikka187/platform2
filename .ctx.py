"""按关键词打印 PDF 文本上下文，便于快速定位章节与表格。"""
import re
import sys
from pathlib import Path

TXT = Path(r'D:\platform2\.pdftext')
KWS = sys.argv[1].split('||') if len(sys.argv) > 1 else []
WIN = int(sys.argv[2]) if len(sys.argv) > 2 else 420
TAGS = sys.argv[3].split(',') if len(sys.argv) > 3 else ['xuanti', 'lunwen']
MAXHITS = int(sys.argv[4]) if len(sys.argv) > 4 else 6

for tag in TAGS:
    path = TXT / f'{tag}.txt'
    if not path.is_file():
        continue
    blob = path.read_text(encoding='utf-8')
    for kw in KWS:
        hits = [m.start() for m in re.finditer(re.escape(kw), blob)]
        print(f'\n########## [{tag}] "{kw}" 命中 {len(hits)} 次 ##########')
        for h in hits[:MAXHITS]:
            start = max(0, h - WIN // 2)
            snippet = blob[start:h + WIN // 2].replace('\n', ' ')
            page = blob.rfind('<<<PAGE', 0, h)
            page_no = blob[page:page + 20].strip() if page >= 0 else '?'
            print(f'--- {page_no} ---')
            print(snippet)
