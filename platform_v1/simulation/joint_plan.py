"""联合方案：用论文口径的 ALNS 求解器生成"客户→批次→趟次"方案，并对接平台既有模块。

产出与 ``planning.build_plan`` 同构的方案字典（customers / trips / summary / dc / warnings），
因此方案页地图、月台协同、充电排班、成本核算与监控模拟流无需改动即可直接使用；
额外给出 ``alns_summary``（论文 F1+F2 口径的日总配送成本与准时率）。

同时合成求解器口径的装车时序（``schedule``），供月台协同与充电排班使用：
备货开始 = 装车开始 − 备货时长，装车开始 = 发车 − 装车时长，月台按顺序轮转分配。
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Sequence

from simulation import vrp_optimizer as vrp

TURNAROUND_MIN = 30
DOCK_OPEN_MIN = 3 * 60
DOCK_CLOSE_MIN = 12 * 60
PREP_MIN = 15


def _hhmm(minutes) -> str:
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def build_joint_plan(conn: sqlite3.Connection, fleet_tag: str = 'baseline',
                     products: Sequence[str] = ('姜蒜', '鲜面条'), profile: str = 'case2',
                     departure_policy: str = 'ready', restarts: int = 5,
                     iterations: int = 3000, dock_count: Optional[int] = None,
                     seed: int = 42) -> dict:
    """跑 ALNS 联合优化，返回平台统一格式的方案。"""
    vrp.DEPARTURE_POLICY = departure_policy
    problem = vrp.build_problem(conn, fleet_tag=fleet_tag, products=products, profile=profile)
    result = vrp.solve(problem, restarts=restarts, iterations=iterations, seed=seed)
    summary = result['summary']
    trips = result['trips']

    params = {row['key']: row['value'] for row in conn.execute('SELECT key, value FROM params')}
    dock_total = int(dock_count or params.get('_dock_count') or 12)

    by_code = {}
    for product in problem.products:
        for node in problem.nodes[product]:
            by_code[(product, node.code)] = node

    plan_trips = []
    schedule = []
    for index, trip in enumerate(sorted(trips, key=lambda t: (t['planned_departure_min'], t['trip_id']))):
        load_start = max(DOCK_OPEN_MIN, int(round(trip['planned_departure_min'] - max(20, trip['service_total_min']))))
        load_end = int(round(trip['planned_departure_min']))
        dock_code = f'D{index % dock_total + 1}'
        plan_trips.append({
            'trip_id': trip['trip_id'], 'batch_id': trip['batch_id'], 'product': trip['product'],
            'customer_codes': trip['customer_codes'],
            'sequence': [{'code': item['code'], 'name': item.get('name', ''),
                          'leg_km': None, 'arrival_min': item['arrival_min'],
                          'service_start_min': item['service_start_min'],
                          'service_end_min': item['service_end_min']} for item in trip['sequence']],
            'load_kg': trip['load_kg'], 'volume_m3': trip['volume_m3'],
            'distance_km': trip['distance_km'], 'duration_min': trip['duration_min'],
            'service_total_min': trip['service_total_min'],
            'planned_departure_min': trip['planned_departure_min'],
            'vehicle_code': trip['vehicle_code'], 'vehicle_type': trip['vehicle_type'],
            'energy_type': trip['energy_type'], 'capacity_kg': trip['capacity_kg'],
            'capacity_m3': trip['capacity_m3'], 'load_rate': trip['load_rate'],
            'fixed_cost': problem.vehicles[trip['vehicle_type']]['fixed'],
            'unit_cost': problem.vehicles[trip['vehicle_type']]['unit_cost'],
            'maintenance_cost': problem.vehicles[trip['vehicle_type']].get('maintenance'),
            'cost': trip['cost'],
            'dock_code': dock_code, 'load_start': load_start, 'load_end': load_end,
        })
        schedule.append({
            'trip_id': trip['trip_id'], 'vehicle_id': trip['vehicle_code'],
            'vehicle_type': trip['vehicle_type'], 'energy_type': trip['energy_type'],
            'load_kg': trip['load_kg'], 'distance_km': trip['distance_km'],
            'dock_id': dock_code,
            'prep_start': max(DOCK_OPEN_MIN, load_start - PREP_MIN),
            'prep_end': load_start,
            'vehicle_arrival': max(DOCK_OPEN_MIN, load_start - 15),
            'load_start': load_start, 'load_end': load_end,
            'depart': int(round(trip['planned_departure_min'])),
            'wait_min': int(round(trip['wait_min'])),
            'charge_start': '', 'charge_end': '',
        })

    vehicles = []
    for name, spec in problem.vehicles.items():
        for position, code in enumerate(spec['codes']):
            vehicles.append({
                'code': code, 'vehicle_type': name, 'energy_type': spec['energy'][position],
                'capacity_kg': spec['capacity'], 'capacity_m3': spec['volume'],
                'fixed_cost': spec['fixed'], 'unit_cost': spec['unit_cost'],
                'maintenance_cost': spec.get('maintenance'),
                'effective_range_km': spec.get('range'),
            })

    customers = []
    for product in problem.products:
        for node in problem.nodes[product]:
            customers.append({
                'code': node.code, 'name': node.name, 'lon': node.lon, 'lat': node.lat,
                'batch_code': node.batch, 'service_min': node.service,
                'exp_start': node.e, 'exp_end': node.l,
                'acc_start': node.E, 'acc_end': node.L,
                'total_kg': round(node.demand, 2), 'total_m3': round(node.volume, 3),
                'product_scope': product,
                'demands': [{'product_code': ('大蒜' if product == '姜蒜' else '鲜面条'),
                             'demand_kg': node.demand, 'volume_m3': node.volume}],
            })

    warnings = []
    if summary['on_time_rate'] < 100:
        warnings.append({'level': 'info', 'trip': '-',
                         'message': f"期望时间窗准时率 {summary['on_time_rate']}%"
                                    f"（{summary['on_time_customers']}/{summary['customers']} 户），"
                                    f"晚到惩罚 {summary['cost_late']} 元"})
    if summary['reused_trips']:
        warnings.append({'level': 'info', 'trip': '-',
                         'message': f"{summary['reused_trips']} 趟次由同一车辆复用执行"
                                    f"（车辆池 {summary['vehicles_used']} 辆）"})

    return {
        'scope': '全部' if len(problem.products) > 1 else problem.products[0],
        'fleet_tag': fleet_tag,
        'profile': profile,
        'departure_policy': departure_policy,
        'dc': {'lon': problem.dc[0], 'lat': problem.dc[1]},
        'customers': customers,
        'trips': plan_trips,
        'vehicles': vehicles,
        'schedule': schedule,
        'summary': {
            'customers': summary['customers'],
            'served_customers': summary['customers'],
            'trips': summary['trips'],
            'total_kg': summary['total_kg'],
            'total_m3': summary['total_m3'],
            'total_distance_km': summary['total_distance_km'],
            'avg_load_rate': summary['avg_load_rate'],
            'vehicles_used': summary['vehicles_used'],
            'infeasible_trips': 0,
        },
        'alns_summary': summary,
        'warnings': warnings,
        'solver_data': {'orders': [], 'trips': [], 'vehicles': [], 'docks': [], 'prep': [], 'charging': []},
    }


__all__ = ['build_joint_plan']
