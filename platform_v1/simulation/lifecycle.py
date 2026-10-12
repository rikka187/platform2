"""车队全生命周期"日全成本"与碳排（新能源配置 C0 / C3 / C4）。

口径构成（四项）
--------------------------------
1. **车辆折旧（日摊销）** = 购置价 × (1 − 残值率) / (摊销年限 × 365)
2. **能源成本** = 行驶能耗 + 制冷能耗
   * 燃油：``里程 × L/km × 柴油价``；制冷 ``小时 × L/h × 柴油价``
   * 纯电：``里程 × kWh/km × 谷段电价 × 充电损耗``；制冷 ``小时 × kWh/h × 谷段电价``
3. **维护成本** = 按车型日维护费（小型/中型分别）
4. **固定年费（日摊）** = 保险与年检 / 365
5. **碳排** = 燃油 ``L × 2.63 kg/L`` + 电力 ``kWh × 0.1564 kg/kWh``

参数全部来自 ``params`` 表，可在「参数设置」页调整；``CALIBRATION`` 记录当前
默认假设（口径可逐项替换），
便于按实际运营口径逐项核对。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 默认参数（可被 params 表覆盖）
DEFAULTS = {
    # 摊销与固定费
    'amort_years': 8.0,            # 摊销年限
    'residual_rate': 0.05,         # 残值率
    'annual_fixed_small': 9000.0,  # 小型车保险+年检+车船税（元/年）
    'annual_fixed_medium': 13000.0,
    # 能源单价
    'diesel_price': 6.5,           # 元/L
    'power_valley': 0.256,         # 元/kWh（谷段）
    'charge_loss': 1.10,           # 充电损耗系数（充 1.10 kWh 得 1 kWh 入车）
    # 能耗强度（缺省取平台 params 里的车辆单耗，可在「参数设置」调整）
    'fuel_small_l_per_km': 0.118,
    'fuel_medium_l_per_km': 0.166,
    'ev_small_kwh_per_km': 0.42,
    'ev_medium_kwh_per_km': 1.96,
    'fuel_reefer_l_per_h': 0.4,    # 小型燃油制冷；中型按 1.67 倍
    'ev_reefer_kwh_per_h': 1.4,
    # 维护（元/日）
    'maintenance_small': 40.0,
    'maintenance_medium': 140.0,
    # 碳排因子
    'diesel_carbon_factor': 2.63,
    'grid_carbon_factor': 0.1564,
    # 充电服务费（元/kWh，可选）
    'charging_service_fee': 0.0,
}

# 成本口径假设（可在「参数设置」中逐项替换）
CALIBRATION_NOTE = (
    '成本口径假设（可在「参数设置」中调整）：摊销年限 8 年、残值率 5%、'
    '小型车年固定费 0.9 万元、中型车 1.3 万元、维护 40/140 元·日⁻¹、'
    '小型燃油 11.8 L/100km、小型纯电 0.42 kWh/km、中型纯电 1.96 kWh/km、'
    '柴油 6.5 元/L、谷段电价 0.256 元/kWh、充电损耗 10%、'
    '碳排因子燃油 2.63 kg/L、电网 0.1564 kg/kWh。'
)


def _num(params: Dict[str, str], key: str, default: Optional[float] = None) -> float:
    value = (params or {}).get(key)
    if value in (None, ''):
        return float(DEFAULTS.get(key, default if default is not None else 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key, default if default is not None else 0.0))


def vehicle_daily_cost(vehicle: dict, km: float, drive_h: float, service_h: float,
                       params: Dict[str, str]) -> dict:
    """单车日全成本与碳排。"""
    is_ev = str(vehicle.get('energy_type')) == '纯电'
    is_medium = str(vehicle.get('vehicle_type')) == '中型'
    years = _num(params, 'amort_years')
    residual = _num(params, 'residual_rate')
    price = float(vehicle.get('purchase_price_wan') or 0) * 10000.0
    amort = price * (1 - residual) / max(1e-6, years * 365.0)

    if is_ev:
        kwh_per_km = _num(params, 'ev_medium_kwh_per_km' if is_medium else 'ev_small_kwh_per_km')
        reefer_kwh = _num(params, 'ev_reefer_kwh_per_h') * (1.9 if is_medium else 1.0)
        price_kwh = _num(params, 'power_valley') + _num(params, 'charging_service_fee')
        loss = _num(params, 'charge_loss')
        energy_kwh = km * kwh_per_km + (drive_h + service_h) * reefer_kwh
        billed_kwh = energy_kwh * loss
        energy_cost = billed_kwh * price_kwh
        carbon = billed_kwh * _num(params, 'grid_carbon_factor')
        energy_desc = f'{round(energy_kwh, 1)} kWh（计费 {round(billed_kwh, 1)} kWh）'
    else:
        l_per_km = _num(params, 'fuel_medium_l_per_km' if is_medium else 'fuel_small_l_per_km')
        reefer_l = _num(params, 'fuel_reefer_l_per_h') * (1.67 if is_medium else 1.0)
        price_l = _num(params, 'diesel_price')
        fuel_l = km * l_per_km + (drive_h + service_h) * reefer_l
        energy_cost = fuel_l * price_l
        carbon = fuel_l * _num(params, 'diesel_carbon_factor')
        energy_desc = f'{round(fuel_l, 1)} L'

    maintenance = _num(params, 'maintenance_medium' if is_medium else 'maintenance_small')
    annual_fixed = _num(params, 'annual_fixed_medium' if is_medium else 'annual_fixed_small') / 365.0
    total = amort + energy_cost + maintenance + annual_fixed
    return {
        'vehicle_code': vehicle.get('code'), 'vehicle_type': vehicle.get('vehicle_type'),
        'energy_type': vehicle.get('energy_type'), 'km': round(km, 2),
        'amortization': round(amort, 2), 'energy_cost': round(energy_cost, 2),
        'maintenance': round(maintenance, 2), 'fixed_annual': round(annual_fixed, 2),
        'daily_cost': round(total, 2), 'carbon_kg': round(carbon, 3),
        'energy_desc': energy_desc,
    }


def evaluate_fleet(vehicles: List[dict], workload: Dict[str, dict], params: Dict[str, str]) -> dict:
    """按"车队配置 + 当日作业量"计算日全成本与碳排。

    ``workload`` 形如 ``{车辆编号: {'km': , 'drive_h': , 'service_h': }}``；
    未出现在 workload 中的车辆按当日闲置（只计摊销与年费）计。
    """
    rows = []
    for vehicle in vehicles:
        item = workload.get(str(vehicle.get('code')), {})
        rows.append(vehicle_daily_cost(vehicle, float(item.get('km') or 0),
                                       float(item.get('drive_h') or 0),
                                       float(item.get('service_h') or 0), params))
    by_energy: Dict[str, dict] = {}
    for row in rows:
        key = row['energy_type']
        bucket = by_energy.setdefault(key, {'count': 0, 'daily_cost': 0.0, 'carbon_kg': 0.0, 'km': 0.0})
        bucket['count'] += 1
        bucket['daily_cost'] = round(bucket['daily_cost'] + row['daily_cost'], 2)
        bucket['carbon_kg'] = round(bucket['carbon_kg'] + row['carbon_kg'], 3)
        bucket['km'] = round(bucket['km'] + row['km'], 2)
    return {
        'vehicles': rows,
        'by_energy': by_energy,
        'daily_cost': round(sum(r['daily_cost'] for r in rows), 2),
        'carbon_kg': round(sum(r['carbon_kg'] for r in rows), 3),
        'breakdown': {
            'amortization': round(sum(r['amortization'] for r in rows), 2),
            'energy_cost': round(sum(r['energy_cost'] for r in rows), 2),
            'maintenance': round(sum(r['maintenance'] for r in rows), 2),
            'fixed_annual': round(sum(r['fixed_annual'] for r in rows), 2),
        },
        'calibration_note': CALIBRATION_NOTE,
    }


def assign_routes(trips: List[dict], vehicles: List[dict], prefer_ev: bool = True,
                  turnaround_min: float = 30.0) -> Optional[Dict[str, dict]]:
    """把**同一组路线**指派到某个车队的车辆上（任务不变、只换配置）。

    规则（确定性）：按发车时刻先后逐趟指派；候选车必须装得下（载重 + 容积双约束）；
    优先选当前空闲的车，其次**纯电优先**，再其次容量最小。同一辆车可复用，
    复用条件为"上一趟返场 + 周转 ≤ 本趟发车"。任一趟无车可派返回 None（运力不足）。
    """
    fleet = sorted(vehicles, key=lambda v: (
        0 if (prefer_ev and str(v.get('energy_type')) == '纯电') else 1,
        float(v.get('capacity_kg') or 0), str(v.get('code'))))
    free_at = {str(v.get('code')): 0.0 for v in vehicles}
    assignment: Dict[str, dict] = {}
    for trip in sorted(trips, key=lambda t: (t.get('planned_departure_min') or 0, -float(t.get('load_kg') or 0))):
        load = float(trip.get('load_kg') or 0)
        volume = float(trip.get('volume_m3') or 0)
        depart = float(trip.get('planned_departure_min') or 0)
        candidates = [v for v in fleet
                      if load <= float(v.get('capacity_kg') or 0) + 1e-6
                      and volume <= float(v.get('capacity_m3') or 0) + 1e-6]
        if not candidates:
            return None
        ready = [v for v in candidates if free_at[str(v['code'])] <= depart]
        pool = ready or candidates
        best = min(pool, key=lambda v: (
            free_at[str(v['code'])],
            0 if (prefer_ev and str(v.get('energy_type')) == '纯电') else 1,
            float(v.get('capacity_kg') or 0), str(v.get('code'))))
        assignment[str(trip['trip_id'])] = best
        free_at[str(best['code'])] = depart + float(trip.get('duration_min') or 0) + turnaround_min
    return assignment


def evaluate_fleet_on_routes(trips: List[dict], vehicles: List[dict],
                             params: Dict[str, str]) -> Optional[dict]:
    """同一组路线在该车队配置下的日全成本与碳排（含"运力不足"判定）。"""
    assignment = assign_routes(trips, vehicles)
    if assignment is None:
        return None
    workload: Dict[str, dict] = {}
    for trip in trips:
        vehicle = assignment[str(trip['trip_id'])]
        bucket = workload.setdefault(str(vehicle['code']), {'km': 0.0, 'drive_h': 0.0, 'service_h': 0.0})
        bucket['km'] += float(trip.get('distance_km') or 0)
        drive_min = float(trip.get('drive_min') or 0) or float(trip.get('duration_min') or 0)
        bucket['drive_h'] += drive_min / 60.0
        bucket['service_h'] += float(trip.get('service_total_min') or 0) / 60.0
    evaluation = evaluate_fleet(vehicles, workload, params)
    evaluation['assignment'] = {trip_id: v['code'] for trip_id, v in assignment.items()}
    evaluation['used_vehicles'] = len(workload)
    return evaluation


def workload_from_trips(trips: List[dict]) -> Dict[str, dict]:
    """由 ALNS 方案（含每趟车辆、里程、时长）汇总各车的当日作业量。"""
    workload: Dict[str, dict] = {}
    for trip in trips:
        code = str(trip.get('vehicle_code') or '')
        if not code:
            continue
        bucket = workload.setdefault(code, {'km': 0.0, 'drive_h': 0.0, 'service_h': 0.0})
        bucket['km'] += float(trip.get('distance_km') or 0)
        drive_min = float(trip.get('drive_min') or 0) or float(trip.get('duration_min') or 0)
        bucket['drive_h'] += drive_min / 60.0
        bucket['service_h'] += float(trip.get('service_total_min') or 0) / 60.0
    return workload


COMPARE_SCENARIOS = {
    'C0': {'label': '全燃油（现状 10 辆）', 'fleet_tag': 'baseline'},
    'C3': {'label': '推荐方案（6 纯电 + 2 保留燃油 + 3 桩）', 'fleet_tag': 'c3'},
    'C4': {'label': '备选升级（8 纯电 + 2 燃油，纯电比例更高）', 'fleet_tag': 'c4'},
}


__all__ = ['CALIBRATION_NOTE', 'COMPARE_SCENARIOS', 'DEFAULTS', 'evaluate_fleet',
           'vehicle_daily_cost', 'workload_from_trips']
