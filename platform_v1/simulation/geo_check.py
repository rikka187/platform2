"""客户坐标 → 距离矩阵的一致性校核。

用途：
* 管理后台"客户管理"页每次改动坐标后，自动与团队既有 31×31 矩阵比对，
  不一致立即提示（而不是等到算方案时才发现距离不对）；
* ``tools/validate_distance.py`` 复用同一实现做深度校核。

口径见 ``simulation/geo.py``：Haversine + 地球半径 6371.0 km + 保留 2 位小数。
"""
from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from simulation.geo import distance_matrix, haversine_km, round_km

APP_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = APP_ROOT / 'data' / 'case2' / 'reference'

REFERENCE_FILES = {
    '姜蒜': 'matrix_ginger_garlic_distance.csv',
    '鲜面条': 'matrix_noodles_distance.csv',
}


def read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    encoding = 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'gb18030'
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode('utf-8', errors='replace')


def load_reference(scope: str) -> Tuple[List[str], List[List[float]]]:
    path = REFERENCE_DIR / REFERENCE_FILES[scope]
    rows = [row for row in csv.reader(io.StringIO(read_text_any(path))) if any(c.strip() for c in row)]
    labels = [c.strip() for c in rows[0][1:]]
    matrix = [[float(c) for c in row[1:]] for row in rows[1:]]
    return labels, matrix


def label_code(label: str) -> str:
    if '仓库' in label or '配送中心' in label:
        return '0'
    return ''.join(ch for ch in label if ch.isdigit())


def depot_coords(conn: sqlite3.Connection) -> Tuple[float, float]:
    params = {row['key']: row['value'] for row in conn.execute('SELECT key, value FROM params')}
    return float(params.get('dc_lon', 104.26)), float(params.get('dc_lat', 30.85))


def current_coords(conn: sqlite3.Connection, scope: str) -> Dict[str, Tuple[float, float]]:
    rows = conn.execute('SELECT code, lon, lat FROM customers WHERE product_scope = ? AND active = 1', (scope,))
    return {str(row['code']).strip(): (float(row['lon']), float(row['lat'])) for row in rows}


def build_distance_matrix(conn: sqlite3.Connection, scope: str,
                          codes: Optional[List[str]] = None) -> Tuple[List[str], List[List[float]]]:
    """按当前库内坐标生成距离矩阵；``codes`` 为空时取该品类全部客户（按编号排序）。"""
    depot = depot_coords(conn)
    points = current_coords(conn, scope)
    if codes is None:
        codes = sorted(points.keys(), key=lambda c: (len(c), c))
    labels = ['配送中心'] + [f'客户{c}' for c in codes]
    coords = [depot] + [points[c] for c in codes]
    return labels, distance_matrix(coords)


def check_scope(conn: sqlite3.Connection, scope: str) -> dict:
    """把当前库内坐标重算的矩阵与既有矩阵逐格比对。"""
    labels, reference = load_reference(scope)
    depot = depot_coords(conn)
    points = current_coords(conn, scope)

    codes = [label_code(label) for label in labels]
    missing = [code for code in codes if code != '0' and code not in points]
    extra = sorted(set(points) - {code for code in codes if code != '0'})

    coords = []
    for code in codes:
        coords.append(depot if code == '0' else points.get(code, (None, None)))

    mismatches = []
    for i in range(len(coords)):
        for j in range(len(coords)):
            if coords[i][0] is None or coords[j][0] is None:
                continue
            mine = round_km(haversine_km(coords[i][0], coords[i][1], coords[j][0], coords[j][1]))
            ref = reference[i][j]
            if abs(mine - ref) > 1e-9:
                mismatches.append({
                    'from': labels[i], 'to': labels[j], 'reference': ref, 'current': mine,
                })
    compared = sum(1 for i in range(len(coords)) for j in range(len(coords))
                   if coords[i][0] is not None and coords[j][0] is not None)
    return {
        'scope': scope,
        'reference_size': len(labels),
        'compared_cells': compared,
        'matched_customers': len(codes) - len(missing) - 1,
        'missing_customers': missing,
        'extra_customers': extra,
        'mismatches': mismatches[:20],
        'mismatch_count': len(mismatches),
        'max_diff_km': max((abs(m['current'] - m['reference']) for m in mismatches), default=0.0),
        'consistent': not mismatches and not missing,
    }


def check_all(conn: sqlite3.Connection) -> dict:
    results = [check_scope(conn, scope) for scope in REFERENCE_FILES]
    return {
        'radius_km': 6371.0,
        'digits': 2,
        'results': results,
        'consistent': all(item['consistent'] for item in results),
    }


__all__ = [
    'REFERENCE_DIR', 'REFERENCE_FILES', 'build_distance_matrix', 'check_all',
    'check_scope', 'current_coords', 'depot_coords', 'label_code', 'load_reference',
]
