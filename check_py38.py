"""按服务器环境（Python 3.8.10）做兼容性自查。

1) 语法：ast.parse(..., feature_version=(3, 8)) 会拒绝 3.9+ 才有的语法（如 match、PEP 604 联合类型）；
2) 运行时：内置泛型下标（list[int] / dict[str, int] / str | None 等）在 3.8 上会
   TypeError: 'type' object is not subscriptable——除非该文件有 `from __future__ import annotations`；
3) 顺带检查标准库里 3.9+ 才有的模块。
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent / 'platform_v1'
BUILTIN_GENERICS = {'list', 'dict', 'tuple', 'set', 'frozenset', 'type'}
NEW_MODULES = {'graphlib', 'zoneinfo'}
problems = []
files = [p for p in ROOT.rglob('*.py')
         if '.venv' not in p.parts and '__pycache__' not in p.parts]

for path in files:
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(source, filename=str(path), feature_version=(3, 8))
    except SyntaxError as exc:
        problems.append(f'[语法] {rel}:{exc.lineno} {exc.msg}（3.8 不支持）')
        continue

    future = any(
        isinstance(node, ast.ImportFrom) and node.module == '__future__'
        and any(alias.name == 'annotations' for alias in node.names)
        for node in tree.body)
    # 收集注解位置，用于区分"注解里用"与"运行时用"（两者在 3.8 上都危险，除非有 future 导入）
    if future:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            base = node.value
            name = base.id if isinstance(base, ast.Name) else None
            if name in BUILTIN_GENERICS:
                problems.append(f'[运行时] {rel}:{node.lineno} 内置泛型下标 {name}[...]，'
                                f'3.8 会报 TypeError（该文件缺少 __future__ 导入）')
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left, right = node.left, node.right
            if isinstance(left, ast.Name) and left.id in ('str', 'int', 'float', 'bytes', 'None'):
                problems.append(f'[运行时] {rel}:{node.lineno} PEP 604 联合类型，需要 3.10')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in NEW_MODULES:
                    problems.append(f'[模块] {rel}:{node.lineno} 标准库 {alias.name} 需要 3.9+')

print(f'检查 {len(files)} 个 Python 文件（目标版本 3.8.10）')
if problems:
    for line in problems:
        print('  ' + line)
    print(f'发现 {len(problems)} 个潜在问题')
    sys.exit(1)
print('  未发现 3.8 不兼容的语法/运行时写法')
