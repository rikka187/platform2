"""几何计算回归测试：Haversine 实现 + 与团队既有矩阵的逐格一致性。

这里的断言不是"差不多"，而是要求**逐格相等**：只要客户坐标或舍入口径被改动，
测试立刻失败，避免平台算出来的距离与论文里的距离悄悄对不上。
"""
import csv
import io
import math
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation import geo  # noqa: E402

CASE2 = ROOT / 'data' / 'case2'
REF = CASE2 / 'reference'


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
    return labels, [[float(c) for c in row[1:]] for row in rows[1:]]


def customers(scope: str) -> dict:
    path = CASE2 / ('customers_ginger_garlic.csv' if scope == '姜蒜' else 'customers_noodles.csv')
    rows = read_csv_any(path)
    header = [c.strip() for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    return {row[idx['customer_id']].strip(): (float(row[idx['lon']]), float(row[idx['lat']]))
            for row in rows[1:]}


def depot() -> tuple:
    params = {row[0]: row[1] for row in read_csv_any(CASE2 / 'params.csv') if len(row) >= 2}
    return (float(params['dc_lon']), float(params['dc_lat']))


def ordered_coords(scope: str, labels) -> list:
    pts = customers(scope)
    dc = depot()
    out = []
    for label in labels:
        if '仓库' in label or '配送中心' in label:
            out.append(dc)
        else:
            code = ''.join(ch for ch in label if ch.isdigit())
            out.append(pts[code])
    return out


class TestHaversine(unittest.TestCase):
    def test_known_distance(self):
        """配送中心(104.26,30.85) → 姜蒜客户1(104.09,30.69)，既有矩阵为 24.09 km。"""
        value = geo.haversine_km(104.26, 30.85, 104.09, 30.69)
        self.assertAlmostEqual(value, 24.09, places=2)
        self.assertAlmostEqual(geo.round_km(value), 24.09, places=2)

    def test_zero_distance_and_symmetry(self):
        self.assertEqual(geo.haversine_km(104.26, 30.85, 104.26, 30.85), 0.0)
        a = geo.haversine_km(104.19, 30.83, 103.99, 30.68)
        b = geo.haversine_km(103.99, 30.68, 104.19, 30.83)
        self.assertAlmostEqual(a, b, places=9)

    def test_radius_used_by_team_matrices(self):
        """用既有矩阵反推地球半径，应落在 6371 km 附近（±1 km）。"""
        labels, matrix = load_matrix(REF / 'matrix_ginger_garlic_distance.csv')
        coords = ordered_coords('姜蒜', labels)
        ratios = []
        for i in range(len(coords)):
            for j in range(len(coords)):
                if i == j or matrix[i][j] <= 0:
                    continue
                unit = geo.haversine_unit(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                ratios.append(matrix[i][j] / unit)
        self.assertLess(abs(statistics.fmean(ratios) - 6371.0), 1.0)

    def test_time_matrix_conversion(self):
        """时间 = 距离 ÷ 40 km/h × 60。"""
        distances = [[0.0, 7.04], [7.04, 0.0]]
        times = geo.time_matrix(distances, speed_kmh=40.0)
        self.assertAlmostEqual(times[0][1], 10.56, places=4)
        with self.assertRaises(ValueError):
            geo.time_matrix(distances, speed_kmh=0)

    def test_route_distance_return_to_depot(self):
        points = [(104.26, 30.85), (104.19, 30.83), (104.11, 30.64)]
        one_way = geo.route_distance(points, return_to_depot=False)
        round_trip = geo.route_distance(points, return_to_depot=True)
        self.assertGreater(round_trip, one_way)
        self.assertAlmostEqual(
            round_trip,
            geo.round_km(geo.haversine_km(104.26, 30.85, 104.19, 30.83)
                         + geo.haversine_km(104.19, 30.83, 104.11, 30.64)
                         + geo.haversine_km(104.11, 30.64, 104.26, 30.85)),
            places=2)


class TestReferenceMatrices(unittest.TestCase):
    """平台重算的矩阵必须与团队既有矩阵逐格一致（2 位小数口径）。"""

    def check(self, scope: str, path: Path):
        labels, matrix = load_matrix(path)
        coords = ordered_coords(scope, labels)
        mine = geo.distance_matrix(coords, digits=2)
        mismatches = []
        for i in range(len(coords)):
            for j in range(len(coords)):
                if abs(mine[i][j] - matrix[i][j]) > 1e-9:
                    mismatches.append((labels[i], labels[j], matrix[i][j], mine[i][j]))
        self.assertEqual(mismatches, [], f'{scope} 矩阵出现 {len(mismatches)} 处不一致：{mismatches[:5]}')

    def test_ginger_garlic_matrix_exact(self):
        self.check('姜蒜', REF / 'matrix_ginger_garlic_distance.csv')

    def test_noodles_matrix_exact(self):
        self.check('鲜面条', REF / 'matrix_noodles_distance.csv')

    def test_noodles_time_matrix_matches_40kmh(self):
        labels, distances = load_matrix(REF / 'matrix_noodles_distance.csv')
        _, reference_time = load_matrix(REF / 'matrix_noodles_time.csv')
        mine = geo.time_matrix(distances, speed_kmh=40.0)
        worst = max(abs(mine[i][j] - reference_time[i][j])
                    for i in range(len(distances)) for j in range(len(distances)))
        self.assertLessEqual(worst, 0.01, f'时间矩阵最大偏差 {worst:.4f} min')

    def test_suspect_time_matrix_is_not_40kmh(self):
        """存疑副本不能用：其斜率明显偏离 1.5 min/km（40 km/h）。"""
        _, distances = load_matrix(REF / 'matrix_noodles_distance.csv')
        _, suspect = load_matrix(REF / 'matrix_noodles_time_bad.csv')
        pairs = [(distances[i][j], suspect[i][j])
                 for i in range(len(distances)) for j in range(len(distances)) if i != j]
        mx = statistics.fmean(p[0] for p in pairs)
        my = statistics.fmean(p[1] for p in pairs)
        sxx = sum((p[0] - mx) ** 2 for p in pairs)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
        slope = sxy / sxx
        self.assertGreater(abs(slope - 1.5), 0.1, '存疑副本的斜率与 40 km/h 口径接近，结论需要复核')

    def test_matrix_is_symmetric_and_zero_diagonal(self):
        for _, path in (('姜蒜', REF / 'matrix_ginger_garlic_distance.csv'),
                        ('鲜面条', REF / 'matrix_noodles_distance.csv')):
            _, matrix = load_matrix(path)
            n = len(matrix)
            for i in range(n):
                self.assertEqual(matrix[i][i], 0.0)
                for j in range(n):
                    self.assertAlmostEqual(matrix[i][j], matrix[j][i], places=6)


if __name__ == '__main__':
    unittest.main()
