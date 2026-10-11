"""成本与 KPI 核算（选题表2-5 / 表2-6 口径）。

求解器（启发式 / CP-SAT）只负责"哪辆车跑哪趟、哪个月台、几点装车发车"；
本模块按选题给定的系数把方案**定价**，因此换口径只改这里，不动算法。

成本构成
--------
1. 运输成本 = Σ(出车固定成本 + 单位里程成本 × 里程)            （表2-5）
2. 制冷成本 = 8.5 元/h × 行驶时间 + 10 元/h × 服务时间           （表2-6）
3. 时间窗惩罚 = 5 元/h × 早到时间 + 15 元/h × 晚到时间           （表2-6，对标**期望窗**）
4. 货损成本 = 配送货损率 0.008 × 货值 + 服务货损率 0.0016 × 货值 （表2-6）
5. 碳排 = 燃油 2.63 kgCO₂/L × 油耗；纯电 0.1564 kgCO₂/kWh × 电耗（第三章）

KPI：总成本、成本构成、准时率、车辆利用率、平均装载率、总里程、总碳排、
月台等待、订单到发车周期。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DEFAULTS = {
    'reefer_delivery_cost': 8.5,     # 元/小时
    'reefer_service_cost': 10.0,     # 元/小时
    'penalty_early': 5.0,            # 元/小时
    'penalty_late': 15.0,            # 元/小时
    'loss_rate_delivery': 0.008,
    'loss_rate_service': 0.0016,
    'avg_speed_kmh': 40.0,
    'diesel_price': 6.5,
    'power_valley': 0.256,
    'diesel_carbon_factor': 2.63,
    'grid_carbon_factor': 0.1564,
    'carbon_price': 0.095,
    'maintenance_small': 40.0,
    'maintenance_medium': 140.0,
}

# 各车型的能耗口径（第三章）
ENERGY = {
    ('小型', '燃油'): {'fuel_l_per_km': 0.118, 'reefer_l_per_h': 0.4},
    ('小型', '纯电'): {'kwh_per_km': 0.42, 'reefer_kwh_per_h': 1.4},
    ('中型', '燃油'): {'fuel_l_per_km': 0.166, 'reefer_l_per_h': 0.67},
    ('中型', '纯电'): {'kwh_per_km': 1.96, 'reefer_kwh_per_h': 4.0},
}


def _num(params: Dict[str, str], key: str) -> float:
    value = (params or {}).get(key)
    if value in (None, ''):
        return float(DEFAULTS[key])
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def energy_of(vehicle_type: str, energy_type: str) -> Dict[str, float]:
    return ENERGY.get((vehicle_type, energy_type), ENERGY[('小型', '燃油')])


def goods_value(trip_customers: List[dict], products: Dict[str, dict]) -> float:
    """货值 = Σ 各品类需求量 × 单价（表2-6：生姜 3.0、大蒜 3.2、鲜面条 3.5）。"""
    total = 0.0
    for customer in trip_customers:
        for demand in customer.get('demands', []):
            price = float((products.get(demand['product_code']) or {}).get('unit_price') or 0)
            total += float(demand.get('demand_kg') or 0) * price
    return total


def price_trip(trip: dict, plan_customers: Dict[str, dict], products: Dict[str, dict],
               params: Dict[str, str], schedule_entry: Optional[dict] = None) -> dict:
    """给单趟趟次定价与碳排。``schedule_entry`` 为求解器返回的装车/发车时序（可缺省）。"""
    vehicle = trip.get('vehicle') or {}
    distance = float(trip.get('distance_km') or 0)
    vehicle_type = trip.get('vehicle_type') or vehicle.get('vehicle_type') or '小型'
    energy_type = trip.get('energy_type') or vehicle.get('energy_type') or '燃油'
    speed = _num(params, 'avg_speed_kmh') or 40.0

    # 行驶/服务时间：优先用求解器给出的装车-发车时刻，否则用预处理估算
    drive_h = distance / speed
    service_min = float(trip.get('service_total_min') or 0)
    if schedule_entry:
        load_start = schedule_entry.get('load_start')
        load_end = schedule_entry.get('load_end')
        if load_start is not None and load_end is not None:
            service_min = max(service_min, float(load_end) - float(load_start))
    service_h = service_min / 60.0

    # 车辆成本优先取趟次自带字段（预处理层已带出），其次取车辆字典
    fixed_cost = float(trip.get('fixed_cost') or vehicle.get('fixed_cost') or 0)
    unit_cost = float(trip.get('unit_cost') or vehicle.get('unit_cost') or 0)
    transport = fixed_cost + unit_cost * distance

    reefer = _num(params, 'reefer_delivery_cost') * drive_h + _num(params, 'reefer_service_cost') * service_h

    # 时间窗惩罚：逐客户按期望窗计算
    early_h = late_h = 0.0
    on_time = 0
    stops = trip.get('sequence') or []
    for stop in stops:
        customer = plan_customers.get(str(stop.get('code')))
        if not customer:
            continue
        arrival = stop.get('arrival_min')
        exp_start, exp_end = customer.get('exp_start'), customer.get('exp_end')
        if arrival is None:
            continue
        if exp_start is not None and arrival < exp_start:
            early_h += (exp_start - arrival) / 60.0
        elif exp_end is not None and arrival > exp_end:
            late_h += (arrival - exp_end) / 60.0
        else:
            on_time += 1
    penalty = _num(params, 'penalty_early') * early_h + _num(params, 'penalty_late') * late_h

    customers = [plan_customers[str(code)] for code in trip.get('customer_codes', [])
                 if str(code) in plan_customers]
    value = goods_value(customers, products)
    loss = value * (_num(params, 'loss_rate_delivery') + _num(params, 'loss_rate_service'))

    spec = energy_of(vehicle_type, energy_type)
    if energy_type == '纯电':
        energy_kwh = distance * spec['kwh_per_km'] + service_h * spec['reefer_kwh_per_h']
        energy_cost = energy_kwh * _num(params, 'power_valley')
        carbon = energy_kwh * _num(params, 'grid_carbon_factor')
        energy_use = round(energy_kwh, 2)
        energy_unit = 'kWh'
    else:
        fuel_l = distance * spec['fuel_l_per_km'] + service_h * spec['reefer_l_per_h']
        energy_cost = fuel_l * _num(params, 'diesel_price')
        carbon = fuel_l * _num(params, 'diesel_carbon_factor')
        energy_use = round(fuel_l, 2)
        energy_unit = 'L'

    maintenance = float(trip.get('maintenance_cost') or vehicle.get('maintenance_cost') or 0)
    total = transport + reefer + penalty + loss + energy_cost + maintenance
    return {
        'trip_id': trip.get('trip_id'),
        'vehicle_code': trip.get('vehicle_code') or vehicle.get('code'),
        'vehicle_type': vehicle_type,
        'energy_type': energy_type,
        'distance_km': round(distance, 2),
        'drive_h': round(drive_h, 3),
        'service_h': round(service_h, 3),
        'load_kg': round(float(trip.get('load_kg') or 0), 2),
        'volume_m3': round(float(trip.get('volume_m3') or 0), 3),
        'load_rate': float(trip.get('load_rate') or 0),
        'transport_cost': round(transport, 2),
        'reefer_cost': round(reefer, 2),
        'penalty_cost': round(penalty, 2),
        'loss_cost': round(loss, 2),
        'energy_cost': round(energy_cost, 2),
        'maintenance_cost': round(maintenance, 2),
        'carbon_kg': round(carbon, 3),
        'energy_use': energy_use,
        'energy_unit': energy_unit,
        'total_cost': round(total, 2),
        'early_h': round(early_h, 3),
        'late_h': round(late_h, 3),
        'on_time_customers': on_time,
        'customers': len(trip.get('customer_codes') or []),
    }


def price_plan(plan: dict, products: Dict[str, dict], params: Dict[str, str],
               schedule: Optional[List[dict]] = None, used_vehicles: Optional[List[str]] = None) -> dict:
    """对整套方案定价，返回 KPI（与论文 4.7.2 的指标口径一致）。

    若给出求解器的 ``schedule``，则以**求解器实际分配的车辆**为准计算能耗与碳排
    （求解器会做车辆-趟次匹配与多趟次复用，比预处理层的初始分配更准）。
    """
    plan_customers = {str(c['code']): c for c in plan.get('customers', [])}
    vehicles_by_code = {str(v.get('code')): v for v in (plan.get('vehicles') or [])}
    schedule_map = {}
    for entry in schedule or []:
        schedule_map[str(entry.get('trip_id'))] = entry

    details = []
    for trip in plan.get('trips', []):
        entry = schedule_map.get(str(trip.get('trip_id')))
        merged = dict(trip)
        if entry and entry.get('vehicle_code'):
            vehicle = vehicles_by_code.get(str(entry['vehicle_code']))
            if vehicle:
                merged.update({
                    'vehicle_code': vehicle['code'],
                    'vehicle_type': vehicle.get('vehicle_type') or trip.get('vehicle_type'),
                    'energy_type': vehicle.get('energy_type') or trip.get('energy_type'),
                    'fixed_cost': vehicle.get('fixed_cost'),
                    'unit_cost': vehicle.get('unit_cost'),
                    'maintenance_cost': vehicle.get('maintenance_cost'),
                })
        details.append(price_trip(merged, plan_customers, products, params, entry))

    total_trips = len(details)
    total_cost = sum(d['total_cost'] for d in details)
    total_customers = sum(d['customers'] for d in details)
    on_time = sum(d['on_time_customers'] for d in details)
    fleet_size = len(plan.get('vehicles') or []) or 1
    vehicles_used = len({d['vehicle_code'] for d in details if d['vehicle_code']})
    if used_vehicles:
        vehicles_used = len({code for code in used_vehicles if code})

    component = {key: round(sum(d[key] for d in details), 2) for key in
                 ('transport_cost', 'reefer_cost', 'penalty_cost', 'loss_cost', 'energy_cost', 'maintenance_cost')}
    return {
        'total_cost': round(total_cost, 2),
        'cost_breakdown': component,
        'transport_cost': component['transport_cost'],
        'reefer_cost': component['reefer_cost'],
        'penalty_cost': component['penalty_cost'],
        'loss_cost': component['loss_cost'],
        'energy_cost': component['energy_cost'],
        'maintenance_cost': component['maintenance_cost'],
        'carbon_kg': round(sum(d['carbon_kg'] for d in details), 2),
        'total_distance_km': round(sum(d['distance_km'] for d in details), 2),
        'trips': total_trips,
        'customers': total_customers,
        'on_time_customers': on_time,
        'on_time_rate': round(on_time / total_customers * 100, 1) if total_customers else 0.0,
        'vehicle_utilization': round(vehicles_used / fleet_size * 100, 1),
        'used_vehicles': vehicles_used,
        'avg_load_rate': round(sum(d['load_rate'] for d in details) / total_trips, 1) if total_trips else 0.0,
        'avg_cost_per_trip': round(total_cost / total_trips, 2) if total_trips else 0.0,
        'avg_cost_per_customer': round(total_cost / total_customers, 2) if total_customers else 0.0,
        'early_hours': round(sum(d['early_h'] for d in details), 2),
        'late_hours': round(sum(d['late_h'] for d in details), 2),
        'energy_total': round(sum(d['energy_use'] for d in details), 2),
        'trip_details': details,
    }


def compare_scenarios(results: List[dict]) -> dict:
    """多情景对比：给出各情景 KPI 与相对上一情景的边际效益。"""
    ordered = sorted(results, key=lambda r: str(r.get('scenario', '')))
    rows = []
    previous = None
    for item in ordered:
        kpi = item.get('kpi') or {}
        row = {'scenario': item.get('scenario'), **{k: kpi.get(k) for k in
               ('total_cost', 'on_time_rate', 'carbon_kg', 'vehicle_utilization',
                'avg_load_rate', 'total_distance_km')}}
        if previous:
            row['delta'] = {
                'cost': round((kpi.get('total_cost') or 0) - (previous.get('total_cost') or 0), 2),
                'carbon': round((kpi.get('carbon_kg') or 0) - (previous.get('carbon_kg') or 0), 2),
                'on_time': round((kpi.get('on_time_rate') or 0) - (previous.get('on_time_rate') or 0), 1),
                'distance': round((kpi.get('total_distance_km') or 0) - (previous.get('total_distance_km') or 0), 2),
            }
        previous = kpi
        rows.append(row)
    baseline = rows[0] if rows else None
    if baseline:
        for row in rows:
            row['vs_baseline'] = {
                'cost_saving': round((baseline['total_cost'] or 0) - (row['total_cost'] or 0), 2),
                'carbon_saving': round((baseline['carbon_kg'] or 0) - (row['carbon_kg'] or 0), 2),
                'on_time_gain': round((row['on_time_rate'] or 0) - (baseline['on_time_rate'] or 0), 1),
            }
    return {'items': rows}


__all__ = ['DEFAULTS', 'ENERGY', 'compare_scenarios', 'energy_of', 'goods_value',
           'price_plan', 'price_trip']
