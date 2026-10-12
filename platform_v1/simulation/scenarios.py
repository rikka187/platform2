"""情景定义：避免情景 ID 与默认等待上限在前后端、求解器之间各写一份。"""
from __future__ import annotations

SCENARIO_IDS = ('H0', 'S0', 'S1', 'C3', 'D3')

SCENARIO_NAMES = {
    'H0': '人工调度基准',
    'S0': '路径优化方案',
    'S1': '时效优化方案',
    'C3': '车辆新能源方案',
    'D3': '仓配全链路协同方案',
}

# 与前端滑杆默认值保持一致（分钟）
DEFAULT_WAIT_MIN = {
    'H0': 120,
    'S0': 72,
    'S1': 60,
    'C3': 60,
    'D3': 18,
}

# H0 为人工调度基准，里程按 1.22 折算；其余情景为优化后口径。
SCENARIO_DISTANCE_FACTOR = {
    'H0': 1.22,
    'S0': 1.0,
    'S1': 1.0,
    'C3': 1.0,
    'D3': 1.0,
}

# 月台等待口径上限：无论滑杆拉到多少，等待时间按 30 分钟截断。
WAIT_CAP_MIN = 30

# 情景车队口径：C3/D3 在可行时优先纯电，两条长线保留燃油。
NEW_ENERGY_KEEP_FUEL_TRIPS = ('R2', 'R4')


def wait_floor_min(wait_min) -> int:
    """把滑杆值换算成实际生效的等待口径（与启发式求解器保持一致）。"""
    try:
        value = float(wait_min)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return int(min(value, WAIT_CAP_MIN))


__all__ = [
    'SCENARIO_IDS',
    'SCENARIO_NAMES',
    'DEFAULT_WAIT_MIN',
    'SCENARIO_DISTANCE_FACTOR',
    'NEW_ENERGY_KEEP_FUEL_TRIPS',
    'WAIT_CAP_MIN',
    'wait_floor_min',
]
