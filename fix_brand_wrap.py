"""修复侧栏品牌副标题换行：加宽侧栏 + logo 略缩 + 副标题单行。

修改前：236 − 28 − 16 − 46 − 10 = 136px 可用，文字需 154px → 挤掉最后一个字。
修改后：256 − 28 − 16 − 42 − 10 = 160px 可用，文字 147px（10.5px 字号）→ 余量 13px。
"""
import pathlib

CSS = pathlib.Path(r'D:\platform2\platform_v1\app\static\admin.css')
text = CSS.read_text(encoding='utf-8')

pairs = [
    # 侧栏加宽 236 → 256
    ('.shell{display:grid;grid-template-columns:236px 1fr;min-height:100vh}',
     '.shell{display:grid;grid-template-columns:256px 1fr;min-height:100vh}'),
    # logo 46 → 42
    ('.shell .side .brand img.mark-logo{width:46px;height:46px;border-radius:13px;background:#fff;padding:4px;',
     '.shell .side .brand img.mark-logo{width:42px;height:42px;border-radius:12px;background:#fff;padding:4px;'),
    # 副标题单行 + 小一号字 + 省略号兜底
    ('.shell .side .brand span{font-size:11px;color:#a9cbe6}',
     '.shell .side .brand>div{min-width:0}\n'
     '.shell .side .brand span{font-size:10.5px;color:#a9cbe6;display:block;white-space:nowrap;'
     'overflow:hidden;text-overflow:ellipsis;letter-spacing:.2px}'),
]

changed, missed = 0, []
for old, new in pairs:
    if old in text:
        text = text.replace(old, new, 1)
        changed += 1
    else:
        missed.append(old[:70])
CSS.write_text(text, encoding='utf-8')
print(f'admin.css 修改 {changed} 处')
for item in missed:
    print('  未匹配：' + item)

# 复核：把宽度算式重新算一遍
import re
text = CSS.read_text(encoding='utf-8')
side = int(re.search(r'grid-template-columns:(\d+)px', text).group(1))
logo = int(re.search(r'img\.mark-logo\{width:(\d+)px', text).group(1))
font = float(re.search(r'\.brand span\{font-size:([\d.]+)px', text).group(1))
available = side - 28 - 16 - logo - 10
needed = 14 * font
print(f'侧栏 {side}px｜logo {logo}px｜字号 {font}px')
print(f'文字可用 {available:.0f}px，需要 {needed:.0f}px，余量 {available - needed:.0f}px '
      f'→ {"单行可容纳" if available >= needed else "仍会换行或截断"}')
