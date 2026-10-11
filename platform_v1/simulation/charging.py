"""新能源充电排班与校验（第三章 C3 口径）。

口径
----
* 充电桩：3 个 × 120 kW，效率 0.95；
* SOC 区间 20%-80%，单次 40-50 分钟（取 45 分钟）；
* 只排**谷段**（01:00-07:00），电价 0.256 元/kWh；四川电网排放因子 0.1564 kgCO₂/kWh；
* 每辆纯电车在**首次发车之前**完成一次充电；装车/发车时序来自求解器。

校验（产出 conflicts）
----------------------
1. 充电时段必须落在谷段内；
2. 同一充电桩上的时段不得重叠；
3. 充电结束不得晚于该车首次发车时刻（否则发车时电量不足）；
4. 纯电车一趟里程不得超过有效续航（与 planning 的可行路线判定一致，二次校验）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DEFAULTS = {
    'charger_count': 3,
    'charger_power_kw': 120.0,
    'charger_efficiency': 0.95,
    'power_valley': 0.256,
    'grid_carbon_factor': 0.1564,
    'ev_effective_range_small': 135.0,
    'ev_effective_range_medium': 189.0,
}
VALLEY_START = 1 * 60          # 01:00
VALLEY_END = 7 * 60            # 07:00
SESSION_MINUTES = 45           # 单次快充 40-50 分钟
SOC_USABLE = 0.60              # 20% → 80%


def _num(params: Dict[str, str], key: str) -> float:
    value = (params or {}).get(key)
    if value in (None, ''):
        return float(DEFAULTS[key])
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def _hhmm(minutes) -> str:
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def _minutes(text) -> int:
    if isinstance(text, (int, float)):
        return int(text)
    hours, _, mins = str(text or '0:00').partition(':')
    try:
        return int(hours) * 60 + int(mins or 0)
    except ValueError:
        return 0


def plan_charging(vehicles: List[dict], schedule: List[dict], params: Dict[str, str]) -> dict:
    """给纯电车排谷段充电计划，并做冲突校验。

    ``vehicles`` 为方案使用的车辆（含 energy_type / effective_range_km），
    ``schedule`` 为求解器返回的装车发车时序（含 vehicle_id / depart / trip_id）。
    """
    charger_count = int(_num(params, 'charger_count'))
    power = _num(params, 'charger_power_kw')
    efficiency = _num(params, 'charger_efficiency')
    price = _num(params, 'power_valley')
    carbon_factor = _num(params, 'grid_carbon_factor')

    # 每辆车的首次发车时刻与该车执行的最远趟次里程
    first_depart: Dict[str, int] = {}
    max_distance: Dict[str, float] = {}
    trips_by_vehicle: Dict[str, List[str]] = {}
    distance_by_trip = {entry.get('trip_id'): entry.get('distance_km') for entry in schedule}
    for entry in schedule:
        code = str(entry.get('vehicle_id') or '')
        if not code:
            continue
        depart = entry.get('depart')
        if depart is not None:
            first_depart[code] = min(first_depart.get(code, 10 ** 9), int(depart))
        trips_by_vehicle.setdefault(code, []).append(str(entry.get('trip_id')))
        value = distance_by_trip.get(entry.get('trip_id'))
        if value:
            max_distance[code] = max(max_distance.get(code, 0.0), float(value))

    evs = [v for v in vehicles if v.get('energy_type') == '纯电']
    charger_free = {f'C{i + 1}': VALLEY_START for i in range(charger_count)}
    sessions: List[dict] = []
    conflicts: List[dict] = []
    energy_kwh = power * (SESSION_MINUTES / 60.0) * efficiency * SOC_USABLE

    for vehicle in sorted(evs, key=lambda v: first_depart.get(str(v['code']), 10 ** 9)):
        code = str(vehicle['code'])
        depart = first_depart.get(code)
        # 尽量贴着发车前完成充电；没有排班的电车排在谷段末尾
        latest_end = (depart - 10) if depart is not None else VALLEY_END
        latest_end = min(latest_end, VALLEY_END)
        start = latest_end - SESSION_MINUTES
        charger = min(charger_free, key=lambda name: (charger_free[name], name))
        if start < charger_free[charger]:
            start = charger_free[charger]
        end = start + SESSION_MINUTES
        if depart is not None and end > depart:
            conflicts.append({'level': 'warn', 'type': '充电晚于发车',
                              'message': f"{code} 充电结束 {_hhmm(end)} 晚于首次发车 {_hhmm(depart)}",
                              'suggestion': '提前充电时段或调整该车趟次'})
        if start < VALLEY_START:
            start = VALLEY_START
            end = start + SESSION_MINUTES
        in_valley = end <= VALLEY_END
        if not in_valley:
            conflicts.append({'level': 'error', 'type': '谷段溢出',
                              'message': f"{code} 的充电排到 {_hhmm(start)}-{_hhmm(end)}，已超出谷段 "
                                         f"{_hhmm(VALLEY_START)}-{_hhmm(VALLEY_END)}",
                              'suggestion': '增加充电桩、允许平段充电，或减少纯电车投入'})
        charger_free[charger] = end

        distance = max_distance.get(code, 0.0)
        effective = float(vehicle.get('effective_range_km') or 0)
        if effective and distance > effective:
            conflicts.append({'level': 'error', 'type': '续航不足',
                              'message': f"{code} 最长趟次 {distance:.1f} km 超过有效续航 {effective:.0f} km",
                              'suggestion': '改用燃油车或调整线路'})

        sessions.append({
            'vehicle_code': code, 'charger_code': charger,
            'start_min': start, 'end_min': end,
            'start': _hhmm(start), 'end': _hhmm(end),
            'in_valley': in_valley,
            'energy_kwh': round(energy_kwh, 2),
            'cost_yuan': round(energy_kwh * price, 2),
            'carbon_kg': round(energy_kwh * carbon_factor, 3),
            'trips': trips_by_vehicle.get(code, []),
        })

    # 校验：同一充电桩不得重叠
    by_charger: Dict[str, List[dict]] = {}
    for session in sessions:
        by_charger.setdefault(session['charger_code'], []).append(session)
    for charger, items in by_charger.items():
        items.sort(key=lambda s: s['start_min'])
        for previous, current in zip(items, items[1:]):
            if current['start_min'] < previous['end_min']:
                conflicts.append({'level': 'error', 'type': '桩位冲突',
                                  'message': f"{charger} 上 {previous['vehicle_code']} 与 "
                                             f"{current['vehicle_code']} 的充电时段重叠",
                                  'suggestion': '错开充电时段或改用其他桩位'})

    total_kwh = sum(s['energy_kwh'] for s in sessions)
    return {
        'sessions': sessions,
        'charger_count': charger_count,
        'charger_power_kw': power,
        'valley_window': f'{_hhmm(VALLEY_START)}-{_hhmm(VALLEY_END)}',
        'conflicts': conflicts,
        'summary': {
            'ev_count': len(evs),
            'in_valley_sessions': sum(1 for s in sessions if s.get('in_valley')),
            'sessions': len(sessions),
            'total_kwh': round(total_kwh, 2),
            'total_cost': round(sum(s['cost_yuan'] for s in sessions), 2),
            'total_carbon': round(sum(s['carbon_kg'] for s in sessions), 3),
        },
    }


__all__ = ['SESSION_MINUTES', 'VALLEY_END', 'VALLEY_START', 'plan_charging']
