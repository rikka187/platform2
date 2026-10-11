"""读取第四章（平台章）相关 docx 的正文与表格，输出到 .docxtext/。"""
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

SRC = Path(r'C:\Users\rikka\Desktop\物流设计大赛')
OUT = Path(r'D:\platform2\.docxtext')
OUT.mkdir(exist_ok=True)

FILES = [
    SRC / 'my work' / '4(2)' / '第四章4.3数据资源与系统接口设计.docx',
    SRC / 'my work' / '4(2)' / '第四章4.5.4-4.5.5多情景评价与监控反馈.docx',
    SRC / 'my work' / '4(2)' / '第四章分工.docx',
    SRC / 'my work' / '第四章' / '4.2 外部参数来源与情景假设.docx',
    SRC / 'my work' / '第四章' / '4.4.docx',
    SRC / 'my work' / '第四章' / '4.3无人配送无人配送适用条件与时空路权判定.docx',
]


def iter_blocks(document):
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            yield Paragraph(child, document)
        elif tag == 'tbl':
            yield Table(child, document)


for path in FILES:
    if not path.is_file():
        print(f'!! 缺失 {path.name}')
        continue
    document = Document(str(path))
    lines = []
    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = block.style.name if block.style is not None else ''
            prefix = '# ' if style.startswith('Heading') or style.startswith('标题') else ''
            lines.append(f'{prefix}{text}')
        else:
            lines.append('[表格]')
            for row in block.rows:
                cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                lines.append(' | '.join(cells))
            lines.append('[/表格]')
    blob = '\n'.join(lines)
    target = OUT / (path.stem + '.txt')
    target.write_text(blob, encoding='utf-8')
    print(f'{path.name}: {len(blob)} 字符 -> {target.name}')
