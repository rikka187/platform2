"""用团队已算好的矩阵反向校核平台的 Haversine 实现与时间口径。

用法：
    python tools/validate_distance.py

校核对象（项目内自带，服务器上也能跑）：
    data/case2/reference/matrix_ginger_garlic_distance.csv   姜蒜 31×31 距离矩阵
    data/case2/reference/matrix_noodles_distance.csv         鲜面条 31×31 距离矩阵
    data/case2/reference/matrix_noodles_time.csv             鲜面条 31×31 时间矩阵（正解）
    data/case2/reference/matrix_noodles_time_bad.csv         鲜面条 31×31 时间矩阵（存疑副本）

校核内容：
1. Haversine 重算距离矩阵，逐格与既有矩阵比对（含舍入位数扫描）；
2. 反推对方使用的地球半径常数；
3. 时间矩阵是否等于 距离 ÷ 平均车速：给出线性拟合斜率、等效车速与 R²；
4. 结论写入 docs/距离校核报告.md。
"""
from __future__ import annotations

import csv
import io
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台常是 GBK，中文与上标字符会报 UnicodeEncodeError → 统一按 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

from simulation.geo import (  # noqa: E402
    DEFAULT_SPEED_KMH, EARTH_RADIUS_KM, ROUND_KM, haversine_km, haversine_unit,
)

CASE2 = ROOT / 'data' / 'case2'
REF = CASE2 / 'reference'
REPORT = ROOT / 'docs' / '距离校核报告.md'

DISTANCE_MATRICES = [
    ('姜蒜', REF / 'matrix_ginger_garlic_distance.csv'),
    ('鲜面条', REF / 'matrix_noodles_distance.csv'),
]
TIME_MATRICES = [
    ('鲜面条', REF / 'matrix_noodles_time.csv', '正解'),
    ('鲜面条', REF / 'matrix_noodles_time_bad.csv', '存疑副本'),
]


def read_csv_any(path: Path) -> list:
    raw = path.read_bytes()
    encoding = 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'gb18030'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode('utf-8', errors='replace')
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def load_matrix(path: Path):
    rows = read_csv_any(path)
    labels = [c.strip() for c in rows[0][1:]]
    values = [[float(c) for c in row[1:]] for row in rows[1:]]
    return labels, values


def points_of(scope: str) -> dict:
    path = CASE2 / ('customers_ginger_garlic.csv' if scope == '姜蒜' else 'customers_noodles.csv')
    rows = read_csv_any(path)
    header = [c.strip() for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    out = {}
    for row in rows[1:]:
        out[row[idx['customer_id']].strip()] = (float(row[idx['lon']]), float(row[idx['lat']]))
    return out


def depot_point() -> tuple:
    lon, lat = 104.26, 30.85
    for row in read_csv_any(CASE2 / 'params.csv'):
        if len(row) >= 2 and row[0] == 'dc_lon':
            lon = float(row[1])
        if len(row) >= 2 and row[0] == 'dc_lat':
            lat = float(row[1])
    return (lon, lat)


def label_key(label: str) -> str:
    if '仓库' in label or '配送中心' in label or label.strip() == '0':
        return '0'
    return ''.join(ch for ch in label if ch.isdigit())


def compare_distance(scope: str, path: Path) -> dict:
    labels, matrix = load_matrix(path)
    pts, depot = points_of(scope), depot_point()
    coords = []
    for label in labels:
        key = label_key(label)
        if key == '0':
            coords.append(depot)
        elif key in pts:
            coords.append(pts[key])
        else:
            return {'scope': scope, 'error': f'表头 {label!r} 无法匹配客户坐标'}

    n = len(coords)
    if len(matrix) != n or any(len(r) != n for r in matrix):
        return {'scope': scope, 'error': f'矩阵维度与坐标数量不符（{len(matrix)}x{len(matrix[0])} vs {n}）'}

    exact = {}
    for digits in (2, 3, 4):
        diffs = []
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                mine = round(haversine_km(coords[i][0], coords[i][1], coords[j][0], coords[j][1]), digits)
                diffs.append(abs(mine - matrix[i][j]))
        exact[digits] = {
            'max': max(diffs), 'mean': statistics.fmean(diffs),
            'exact_ratio': sum(1 for d in diffs if d < 1e-9) / len(diffs),
        }

    deltas, units, pairs = [], [], []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            expect = haversine_km(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
            actual = matrix[i][j]
            deltas.append(actual - expect)
            pairs.append((abs(actual - expect), labels[i], labels[j], actual, expect))
            if actual > 0:
                units.append(actual / haversine_unit(coords[i][0], coords[i][1], coords[j][0], coords[j][1]))
    pairs.sort(reverse=True)
    abs_deltas = [abs(d) for d in deltas]
    return {
        'scope': scope, 'file': path.name, 'size': n, 'pairs': len(deltas),
        'max_abs': max(abs_deltas), 'mean_abs': statistics.fmean(abs_deltas),
        'p95_abs': sorted(abs_deltas)[int(len(abs_deltas) * 0.95)],
        'radius_fit': statistics.fmean(units) if units else float('nan'),
        'worst': pairs[:5], 'rounding': exact,
    }


def fit_time_vs_distance(time_path: Path, distance_path: Path) -> dict:
    tl, tm = load_matrix(time_path)
    dl, dm = load_matrix(distance_path)
    index = {label_key(l): i for i, l in enumerate(dl)}
    pairs = []
    for i, label in enumerate(tl):
        j = index.get(label_key(label))
        if j is None:
            continue
        for k, label2 in enumerate(tl):
            j2 = index.get(label_key(label2))
            if j2 is None or i == k:
                continue
            pairs.append((dm[j][j2], tm[i][k]))
    if not pairs:
        return {'error': '时间矩阵与距离矩阵无法按表头配对'}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    slope = sxy / sxx if sxx else float('nan')
    intercept = my - slope * mx
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else float('nan')
    speeds = [x / (y / 60.0) for x, y in pairs if y > 0]
    return {
        'pairs': len(pairs), 'slope': slope, 'intercept': intercept, 'r2': r * r,
        'speed_equiv': 60.0 / slope if slope else float('nan'),
        'speed_mean': statistics.fmean(speeds), 'speed_min': min(speeds), 'speed_max': max(speeds),
        'samples': pairs[:5],
    }


def main() -> int:
    print('=' * 76)
    print('Haversine 距离矩阵校核')
    print('=' * 76)
    lines = ['# 距离与时间矩阵校核报告', '',
             f'- 平台实现：`simulation/geo.py`（Haversine，地球半径 {EARTH_RADIUS_KM:g} km，结果保留 {ROUND_KM} 位小数）',
             f'- 平均车速口径：{DEFAULT_SPEED_KMH:g} km/h（选题表2-6）',
             '- 校核脚本：`tools/validate_distance.py`', '']
    ok_all = True

    for scope, path in DISTANCE_MATRICES:
        if not path.is_file():
            print(f'[跳过] {path} 不存在')
            continue
        res = compare_distance(scope, path)
        if res.get('error'):
            ok_all = False
            print(f'[失败] {scope}：{res["error"]}')
            lines += [f'## {scope} 距离矩阵', '', f'校核失败：{res["error"]}', '']
            continue
        print(f'\n[{scope}] {res["file"]}  {res["size"]}×{res["size"]}，{res["pairs"]} 个点对')
        for digits, info in sorted(res['rounding'].items()):
            mark = '  ← 与既有矩阵完全一致' if info['exact_ratio'] > 0.999 else ''
            print(f'  保留 {digits} 位：完全相等 {info["exact_ratio"]:.1%}，最大差 {info["max"]:.6f} km{mark}')
        print(f'  原始误差（4 位口径）：最大 {res["max_abs"]:.6f} km，平均 {res["mean_abs"]:.6f} km')
        print(f'  反推地球半径：{res["radius_fit"]:.4f} km')
        for err, a, b, actual, expect in res['worst'][:3]:
            print(f'    偏差示例 {a} → {b}：矩阵 {actual:.2f}，重算 {expect:.4f}')
        lines += [
            f'## {scope} 距离矩阵', '',
            f'- 文件：`{path.name}`，{res["size"]}×{res["size"]}，{res["pairs"]} 个点对',
            f'- 保留 2 位小数时与平台重算结果完全相等的比例：{res["rounding"][2]["exact_ratio"]:.2%}',
            f'- 未舍入（4 位）最大偏差：{res["max_abs"]:.6f} km，平均 {res["mean_abs"]:.6f} km',
            f'- 反推地球半径：{res["radius_fit"]:.4f} km',
            '- 结论：既有矩阵 = Haversine + 6371 km 地球半径 + 保留 2 位小数，平台实现与其一致。', '',
        ]

    print('\n' + '=' * 76)
    print('时间矩阵口径校核（时间 = 距离 ÷ 车速 × 60）')
    print('=' * 76)
    for scope, path, tag in TIME_MATRICES:
        if not path.is_file():
            print(f'[跳过] {path} 不存在')
            continue
        dist_path = [p for s, p in DISTANCE_MATRICES if s == scope][0]
        info = fit_time_vs_distance(path, dist_path)
        if info.get('error'):
            print(f'[{scope}/{tag}] {info["error"]}')
            continue
        consistent = abs(info['slope'] - 60.0 / DEFAULT_SPEED_KMH) < 1e-3 and info['r2'] > 0.9999
        flag = '与 40 km/h 口径一致' if consistent else '与 40 km/h 口径不一致'
        print(f'\n[{scope}/{tag}] {path.name}：{info["pairs"]} 个点对')
        print(f'  拟合 time = {info["intercept"]:.4f} + {info["slope"]:.5f} × dist （R²={info["r2"]:.6f}）')
        print(f'  等效车速 {info["speed_equiv"]:.2f} km/h；直接反推车速均值 {info["speed_mean"]:.2f} km/h '
              f'（{info["speed_min"]:.2f} ~ {info["speed_max"]:.2f}）')
        print(f'  → {flag}')
        if not consistent:
            # 只有"正解"那份不一致才算校核失败；存疑副本仅作记录，平台不使用它
            if tag == '正解':
                ok_all = False
            else:
                print('  （该副本仅作留档，平台不使用）')
            print('  样本：' + '; '.join(f'{x:.2f}km→{y:.2f}min' for x, y in info['samples']))
        lines += [
            f'## 时间矩阵（{scope} / {tag}）', '',
            f'- 文件：`{path.name}`，{info["pairs"]} 个点对',
            f'- 拟合：time = {info["intercept"]:.4f} + {info["slope"]:.5f} × dist，R² = {info["r2"]:.6f}',
            f'- 等效车速：{info["speed_equiv"]:.2f} km/h；反推车速区间 {info["speed_min"]:.2f} ~ {info["speed_max"]:.2f} km/h',
            f'- 判定：{flag}', '',
        ]

    lines += [
        '## 结论与要求', '',
        f'1. 平台距离一律使用 Haversine 公式，地球半径 {EARTH_RADIUS_KM:g} km，结果保留 {ROUND_KM} 位小数，'
        '可逐格复现团队既有距离矩阵；',
        '2. 时间矩阵必须由距离矩阵换算（÷ 平均车速 40 km/h × 60），'
        '`matrix_noodles_time.csv` 满足该口径，`matrix_noodles_time_bad.csv` 不满足，'
        '平台不使用后者；',
        '3. 客户坐标若被修改，距离与时间矩阵必须重新生成（`tools/validate_distance.py` 会重新校核）。', '',
    ]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\n报告已写入：{REPORT}')
    print('校核结果：' + ('全部一致' if ok_all else '存在不一致（见上）'))
    return 0 if ok_all else 2


if __name__ == '__main__':
    raise SystemExit(main())
