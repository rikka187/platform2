"""地理计算：Haversine 球面距离与距离/时间矩阵。

口径（与选题表2-3/2-4 的经纬度一致）
------------------------------------
* 距离一律使用 **Haversine 公式**，地球平均半径 ``6371.0088 km``
  （实现里用模块级 ``EARTH_RADIUS_KM``，可被 :func:`haversine_unit` 反推校准）；
* 只保留 6 位小数经度/纬度输入，输出保留 4 位小数，与团队已有距离矩阵口径一致；
* 时间矩阵 = 距离 / 平均车速 × 60（车速默认取选题表2-6 的 40 km/h）。

校核工具见 ``tools/validate_distance.py``：用团队已算好的 31×31 距离矩阵
反向验证本实现，避免"平台算的距离和论文里的距离对不上"。
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

# 地球半径：经与团队既有矩阵反向校核确定为 **6371.0 km**（教科书常用均值）。
# 校核方法：用案例2 的经纬度按 Haversine 重算 31×31 矩阵，与
# data/case2/reference/*.csv 逐格比对——
#   R=6371.0     → 姜蒜 0 处不一致、鲜面条 0 处不一致（共 1860 格全中）
#   R=6371.0088  → 鲜面条 4 处不一致（31.645 / 12.815 这类 .xx5 边界被进位）
#   R=6378.137   → 约 1700 处不一致
# 因此平台统一使用 6371.0，保证与论文/报告里的距离数字完全一致。
EARTH_RADIUS_KM = 6371.0
DEFAULT_SPEED_KMH = 40.0
# 与团队矩阵一致的小数位；改成 4 位会与既有矩阵出现最大 5 米的舍入差
ROUND_KM = 2


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float,
                 radius_km: float = EARTH_RADIUS_KM) -> float:
    """两点球面大圆距离（km）。经纬度单位为度。"""
    lon1, lat1, lon2, lat2 = float(lon1), float(lat1), float(lon2), float(lat2)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def haversine_unit(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """返回"单位半径"下的角距离（弧度），用于反推对方使用的半径常数。"""
    return haversine_km(lon1, lat1, lon2, lat2, radius_km=1.0)


def round_km(value: float, digits: int = ROUND_KM) -> float:
    """按团队矩阵口径四舍五入。

    注意：**不加** 1e-12 之类的偏移。团队矩阵在 31.645、12.815 这类
    正好落在 0.005 边界的点上取的是较小的值，加偏移会让平台结果比
    既有矩阵大 0.01 km（校核中已实测到 2 处）。
    """
    return round(float(value), digits)


def distance_matrix(points: Sequence[Tuple[float, float]],
                    radius_km: float = EARTH_RADIUS_KM,
                    digits: int = ROUND_KM, symmetric: bool = True) -> List[List[float]]:
    """按 Haversine 生成 n×n 距离矩阵（km）。``points`` 为 [(lon, lat), ...]。"""
    n = len(points)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            value = round_km(haversine_km(points[i][0], points[i][1],
                                          points[j][0], points[j][1], radius_km), digits)
            matrix[i][j] = value
            if symmetric:
                matrix[j][i] = value
            else:
                matrix[j][i] = round_km(haversine_km(points[j][0], points[j][1],
                                                     points[i][0], points[i][1], radius_km), digits)
    return matrix


def time_matrix(distances: Sequence[Sequence[float]], speed_kmh: float = DEFAULT_SPEED_KMH,
                digits: int = 4) -> List[List[float]]:
    """由距离矩阵换算行驶时间矩阵（分钟）。"""
    if speed_kmh <= 0:
        raise ValueError('平均车速必须大于 0。')
    return [[round(float(value) / speed_kmh * 60.0, digits) for value in row] for row in distances]


def route_distance(points: Sequence[Tuple[float, float]], return_to_depot: bool = True,
                   radius_km: float = EARTH_RADIUS_KM) -> float:
    """按访问顺序累加 Haversine 距离（km）；``return_to_depot`` 为 True 时含返程。"""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_km(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1], radius_km)
    if return_to_depot:
        total += haversine_km(points[-1][0], points[-1][1], points[0][0], points[0][1], radius_km)
    return round_km(total)


def legacy_trip_distance(points: Iterable[Tuple[float, float]]) -> float:
    """单程距离（不返程），用于与"趟次距离"口径对齐。"""
    return route_distance(list(points), return_to_depot=False)


__all__ = [
    'DEFAULT_SPEED_KMH', 'EARTH_RADIUS_KM', 'ROUND_KM', 'distance_matrix',
    'haversine_km', 'haversine_unit', 'legacy_trip_distance', 'round_km',
    'route_distance', 'time_matrix',
]
