"""客户 → 批次 → 趟次 预处理（把客户点转成求解器输入）。

**这不是新的求解算法**：路线分组用"批次归属 + 最近邻 + 载重/容积/时间窗可行性"
这一确定性规则生成候选趟次；车辆-月台-装车时序仍由平台原有的
``simulation.scheduler.solve_scenario``（启发式 / CP-SAT）决定。

口径
----
* 距离：Haversine（``simulation.geo``，R=6371.0 km，2 位小数）；
* 行驶时间：距离 ÷ 平均车速（40 km/h）；
* 装载双约束：重量 ≤ 额定载重 且 体积 ≤ 货厢容积（体积由品类密度折算）；
* 时间窗：期望窗（软）与可接受窗（硬）。到达早于期望窗 → 早到等待；
  晚于可接受窗 → 该点在本趟不可行，另起一趟；单点仍不可行则记为不可行并交人工处理；
* 批次归属取自客户主数据，起送时刻即该批次的最早发车时间。
"""
from __future__ import annotations

import math
import sqlite3
from typing import Dict, List, Optional, Tuple

from simulation.geo import DEFAULT_SPEED_KMH, haversine_km, round_km

# 与求解器对齐的口径
PREP_MIN_DEFAULT = 15          # 备份（拣货/组盘）标准时长
DOCK_DEFAULT_COUNT = 12        # 抽象月台池大小（不铺 252 个真实月台）
DOCK_OPEN_MIN = 3 * 60         # 凌晨 3:00 起可装车（装车高峰 03:00-07:00）
DOCK_CLOSE_MIN = 12 * 60       # 12:00 收工
TURNAROUND_MIN = 30            # 车辆返场后的周转时间（与 CP-SAT 的 turnover_min 对齐）
CHARGE_WINDOW = ('01:20', '07:00')   # 谷段（01:00-07:00）
CHARGE_MINUTES = 45            # 单次快充 40-50 分钟，取 45


class PlanningError(RuntimeError):
    """输入数据不足以生成方案。"""


# --------------------------------------------------------------------------
# 读取数据
# --------------------------------------------------------------------------
def load_params(conn: sqlite3.Connection) -> Dict[str, str]:
    return {row['key']: row['value'] for row in conn.execute('SELECT key, value FROM params')}


def load_products(conn: sqlite3.Connection) -> Dict[str, dict]:
    return {row['code']: dict(row) for row in conn.execute('SELECT * FROM products')}


def load_customers(conn: sqlite3.Connection, scope: str) -> List[dict]:
    """返回带需求明细与时间窗的客户列表（按批次、编号排序，保证结果可复现）。"""
    customers = []
    rows = conn.execute(
        'SELECT * FROM customers WHERE product_scope = ? AND active = 1', (scope,)).fetchall()
    for row in rows:
        item = dict(row)
        demands = [dict(r) for r in conn.execute(
            'SELECT product_code, demand_kg, volume_m3 FROM customer_demand WHERE customer_id = ?',
            (row['id'],))]
        item['demands'] = demands
        item['total_kg'] = sum(d['demand_kg'] or 0 for d in demands)
        item['total_m3'] = sum(d['volume_m3'] or 0 for d in demands)
        customers.append(item)
    customers.sort(key=lambda c: (c.get('batch_code') or '未分批', _code_key(c['code'])))
    return customers


def _code_key(code) -> Tuple[int, str]:
    text = str(code)
    return (int(text), '') if text.isdigit() else (10 ** 9, text)


def load_vehicles(conn: sqlite3.Connection, fleet_tag: str) -> List[dict]:
    rows = conn.execute(
        'SELECT * FROM vehicles WHERE fleet_tag = ? AND available = 1', (fleet_tag,)).fetchall()
    items = [dict(r) for r in rows]
    items.sort(key=lambda v: (_code_key(v['code'])))
    return items


# --------------------------------------------------------------------------
# 趟次生成
# --------------------------------------------------------------------------
def _travel_min(distance_km: float, speed_kmh: float) -> float:
    return distance_km / speed_kmh * 60.0


def _fits(customers_kg: float, customers_m3: float, vehicle: dict) -> bool:
    capacity_kg = float(vehicle.get('capacity_kg') or 0)
    capacity_m3 = float(vehicle.get('capacity_m3') or 0)
    if capacity_kg and customers_kg > capacity_kg + 1e-6:
        return False
    if capacity_m3 and customers_m3 > capacity_m3 + 1e-6:
        return False
    return True


def _pick_vehicle(load_kg: float, volume_m3: float, vehicles: List[dict],
                  distance_km: Optional[float] = None) -> Optional[dict]:
    """选能装下、且续航够用的最小车型（小型优先，其次中型）。

    ``distance_km`` 给出时做**新能源可行路线判定**：纯电车的
    有效续航（已计入冬季衰减与冷机折损）必须覆盖本趟里程，否则只能改用燃油车。
    """
    order = {'小型': 0, '中型': 1}
    candidates = []
    for vehicle in vehicles:
        if not _fits(load_kg, volume_m3, vehicle):
            continue
        if distance_km is not None and vehicle.get('energy_type') == '纯电':
            effective = float(vehicle.get('effective_range_km') or vehicle.get('range_km') or 0)
            if effective and distance_km > effective + 1e-6:
                continue
        candidates.append(vehicle)
    if not candidates:
        return None
    candidates.sort(key=lambda v: (order.get(v['vehicle_type'], 9), v.get('unit_cost') or 0, _code_key(v['code'])))
    return candidates[0]


def build_trips(customers: List[dict], vehicles: List[dict], params: Dict[str, str],
                dc: Tuple[float, float]) -> Tuple[List[dict], List[dict]]:
    """把客户按批次聚成趟次；返回 (趟次列表, 告警列表)。"""
    speed = float(params.get('avg_speed_kmh') or DEFAULT_SPEED_KMH)
    warnings: List[dict] = []
    batches: Dict[str, List[dict]] = {}
    for customer in customers:
        batches.setdefault(customer.get('batch_code') or '未分批', []).append(customer)

    # 批次起送时刻（如"批次一 04:30 起送"），由 build_plan 注入
    batch_depart = params.get('_batch_depart') or {}
    if not isinstance(batch_depart, dict):
        batch_depart = {}

    trips: List[dict] = []
    for batch_code in sorted(batches.keys()):
        pending = list(batches[batch_code])
        depart_after = int(batch_depart.get(batch_code) or 0)
        index = 0
        while pending:
            index += 1
            route, remaining, warn = _build_route(pending, vehicles, dc, speed, depart_after, batch_code, index, params)
            pending = remaining
            warnings.extend(warn)
            if route:
                trips.append(route)
            elif remaining:
                # 无法再成趟：剩余的强制单点成趟并标记不可行，避免死循环
                forced = remaining[0]
                trips.append(_single_customer_trip(forced, vehicles, dc, speed, depart_after, batch_code, index))
                warnings.append({'level': 'error', 'customer': forced['code'],
                                 'message': f"客户 {forced['code']} 无法放入任何趟次（时间窗或装载受限），已单独成趟待人工确认。"})
                pending = remaining[1:]
    return trips, warnings


def _build_route(pending: List[dict], vehicles: List[dict], dc: Tuple[float, float], speed: float,
                 depart_after: int, batch_code: str, index: int,
                 params: Optional[Dict[str, str]] = None):
    """从 pending 中贪心构造一条趟次（最近邻 + 双约束 + 时间窗可行性）。"""
    warnings: List[dict] = []
    current = dc
    clock = float(depart_after)
    load_kg = load_m3 = 0.0
    distance_so_far = 0.0
    chosen: List[dict] = []
    remaining = list(pending)

    while remaining:
        # 候选排序：先按**期望窗结束时刻**（最早截止优先，EDD），再按到当前点的距离，最后按编号。
        # 纯最近邻会把时窗差异大的客户塞进同一趟，一个发车时刻无法同时满足两端
        # （表现为早到与晚到同时出现）；EDD 让同一趟的时窗自然聚拢。
        candidates = sorted(remaining, key=lambda c: (
            c.get('exp_end') if c.get('exp_end') is not None else 10 ** 9,
            haversine_km(current[0], current[1], c['lon'], c['lat']),
            _code_key(c['code'])))
        picked = None
        for customer in candidates:
            leg = haversine_km(current[0], current[1], customer['lon'], customer['lat'])
            arrival = clock + _travel_min(leg, speed)
            service = float(customer.get('service_min') or 0)
            acc_end = customer.get('acc_end')
            if acc_end is not None and arrival > acc_end + 1e-6:
                continue                      # 硬约束：可接受窗已过
            new_kg = load_kg + (customer['total_kg'] or 0)
            new_m3 = load_m3 + (customer['total_m3'] or 0)
            back = haversine_km(customer['lon'], customer['lat'], dc[0], dc[1])
            tentative_distance = distance_so_far + leg + back
            vehicle = _pick_vehicle(new_kg, new_m3, vehicles, tentative_distance)
            if vehicle is None:
                continue                      # 双约束或续航不满足
            # 时窗一致性检查：把该客户加入本趟后，必须存在一个发车时刻让**全部站点都不晚到**。
            # 否则本趟到此为止，另起一趟——这样趟次会按时间窗自然聚类（与团队求解结果同构）。
            tentative_stops = [(item['customer'], item['leg_km']) for item in chosen] + [(customer, round_km(leg))]
            probe = optimise_departure(tentative_stops, depart_after, speed, params)
            if probe['late_h'] > 0 and chosen:
                continue                      # 加了他会有人晚到 → 留给下一趟
            picked = (customer, leg, arrival, service, vehicle, new_kg, new_m3, tentative_distance)
            break

        if picked is None:
            break

        customer, leg, arrival, service, vehicle, new_kg, new_m3, tentative_distance = picked
        start_service = arrival
        exp_start = customer.get('exp_start')
        if exp_start is not None and arrival < exp_start:
            warnings.append({'level': 'info', 'customer': customer['code'],
                             'message': f"客户 {customer['code']} 早到 {int(exp_start - arrival)} 分钟（产生等待与早到惩罚）"})
            start_service = float(exp_start)
        clock = start_service + service
        chosen.append({'customer': customer, 'leg_km': round_km(leg), 'arrival_min': round(arrival, 1),
                       'service_start_min': round(start_service, 1), 'service_end_min': round(clock, 1),
                       'vehicle': vehicle})
        load_kg, load_m3 = new_kg, new_m3
        distance_so_far = tentative_distance - back
        current = (customer['lon'], customer['lat'])
        remaining.remove(customer)

    if not chosen:
        return None, remaining, warnings

    back_leg = haversine_km(current[0], current[1], dc[0], dc[1])
    distance = round_km(sum(item['leg_km'] for item in chosen) + back_leg)
    service_total = sum(float(item['customer'].get('service_min') or 0) for item in chosen)
    duration_min = distance / speed * 60.0 + service_total
    vehicle = _pick_vehicle(load_kg, load_m3, vehicles, distance) or chosen[-1]['vehicle']

    # 发车时刻优化：批次起送时刻只是就绪时间，允许延后以对准客户时间窗
    sequence = [(item['customer'], item['leg_km']) for item in chosen]
    best = optimise_departure(sequence, depart_after, speed, params)
    for item, timeline in zip(chosen, best['timeline']):
        item['arrival_min'] = timeline['arrival_min']
        item['service_start_min'] = timeline['service_start_min']
        item['service_end_min'] = timeline['service_end_min']
    if best['early_h'] > 0:
        warnings.append({'level': 'info', 'trip': f'{batch_code}-T{index}',
                         'message': f"趟次 {batch_code}-T{index} 早到合计 {best['early_h']} 小时（已按最优发车时刻 {_clock_text(best['depart_min'])} 发车）"})
    if best['late_h'] > 0:
        warnings.append({'level': 'warn', 'trip': f'{batch_code}-T{index}',
                         'message': f"趟次 {batch_code}-T{index} 晚到合计 {best['late_h']} 小时，建议人工调整顺序或加车"})

    trip_id = f'{batch_code}-T{index}'
    trip = {
        'trip_id': trip_id,
        'batch_id': batch_code,
        'product': chosen[0]['customer']['product_scope'],
        'customer_codes': [item['customer']['code'] for item in chosen],
        'stops': chosen,
        'load_kg': round(load_kg, 2),
        'volume_m3': round(load_m3, 3),
        'distance_km': round_km(distance),
        'duration_min': round(duration_min, 1),
        'service_total_min': round(service_total, 1),
        'planned_departure_min': int(best['depart_min']),
        'early_h': best['early_h'],
        'late_h': best['late_h'],
        'vehicle': vehicle,
        'capacity_kg': float(vehicle.get('capacity_kg') or 0),
        'capacity_m3': float(vehicle.get('capacity_m3') or 0),
        'load_rate': round(load_kg / float(vehicle.get('capacity_kg') or 1) * 100, 1),
    }
    return trip, remaining, warnings


def _clock_text(minutes) -> str:
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def _range_ok(vehicle: dict, distance_km: float) -> bool:
    """新能源可行路线判定：纯电有效续航必须覆盖本趟里程。"""
    if vehicle.get('energy_type') != '纯电':
        return True
    effective = float(vehicle.get('effective_range_km') or vehicle.get('range_km') or 0)
    return not effective or distance_km <= effective + 1e-6


def assign_vehicles(trips: List[dict], vehicles: List[dict],
                    turnaround_min: int = TURNAROUND_MIN) -> List[dict]:
    """把趟次分配到具体车辆上（决定"用了几辆车、谁跑哪趟"）。

    规则（确定性，可复现）：
    1. 只有**装得下**（载重+容积双约束）且**续航够**的车辆才有资格；
    2. 优先复用"已经返回、可以再跑一趟"的车（多趟次复用），
       其次是还没用过的车，最后才是最早空出来的车；
    3. 车辆返回时刻 = 发车 + 趟次时长 + 周转时间。
    """
    available_at = {v['code']: 0 for v in vehicles}
    used_count = {v['code']: 0 for v in vehicles}
    for trip in sorted(trips, key=lambda t: (t['planned_departure_min'], t['trip_id'])):
        eligible = [v for v in vehicles
                    if _fits(trip['load_kg'], trip['volume_m3'], v) and _range_ok(v, trip['distance_km'])]
        if not eligible:
            trip['vehicle'] = vehicles[0] if vehicles else {}
            trip['vehicle_note'] = '没有同时满足载重/容积/续航的车辆，已按最大可用车处理'
            continue
        depart = float(trip['planned_departure_min'])
        ready = [v for v in eligible if available_at[v['code']] <= depart]
        pool = ready or eligible
        pool.sort(key=lambda v: (0 if (v in ready and used_count[v['code']] > 0) else 1,
                                 used_count[v['code']], available_at[v['code']],
                                 v.get('unit_cost') or 0, _code_key(v['code'])))
        vehicle = pool[0]
        trip['vehicle'] = vehicle
        trip['vehicle_reuse'] = used_count[vehicle['code']] > 0
        used_count[vehicle['code']] += 1
        available_at[vehicle['code']] = depart + float(trip['duration_min']) + turnaround_min
    return trips


def _simulate_route(stops: List[Tuple[dict, float]], depart_min: float,
                    speed: float) -> Tuple[float, float, List[dict], bool]:
    """按给定发车时刻推演一条趟次的时间轴。

    ``stops`` 为 [(客户, 到该客户的距离 km), ...]（访问顺序固定）。
    返回 (早到小时, 晚到小时, 时间轴, 是否满足可接受时间窗硬约束)。
    """
    early_h = late_h = 0.0
    feasible = True
    timeline = []
    clock = float(depart_min)
    for customer, leg in stops:
        arrival = clock + _travel_min(leg, speed)
        service = float(customer.get('service_min') or 0)
        exp_start, exp_end = customer.get('exp_start'), customer.get('exp_end')
        acc_end = customer.get('acc_end')
        if acc_end is not None and arrival > acc_end + 1e-6:
            feasible = False
        if exp_start is not None and arrival < exp_start:
            early_h += (exp_start - arrival) / 60.0
        if exp_end is not None and arrival > exp_end:
            late_h += (arrival - exp_end) / 60.0
        start_service = max(arrival, float(exp_start)) if exp_start is not None else arrival
        clock = start_service + service
        timeline.append({'customer': customer, 'leg_km': round_km(leg), 'arrival_min': round(arrival, 1),
                         'service_start_min': round(start_service, 1), 'service_end_min': round(clock, 1)})
    return early_h, late_h, timeline, feasible


def optimise_departure(stops: List[Tuple[dict, float]], depart_after: int, speed: float,
                       params: Optional[Dict[str, str]] = None, step: int = 5,
                       horizon: int = 300) -> dict:
    """挑选最优发车时刻——批次起送时刻是**就绪时间**，允许延后。

    口径为"批次时间'X 时及以后发车'即就绪时间，允许延后"：
    在 [depart_after, depart_after+horizon] 内按 step 分钟扫描，只接受**不违反可接受
    时间窗**的候选，取"早到+晚到惩罚"最小的最早时刻（确定性、可复现）。
    若全部候选都不可行，退回就绪时刻。
    """
    early_cost = _number(params, 'penalty_early', 5.0)
    late_cost = _number(params, 'penalty_late', 15.0)
    best = None
    for delta in range(0, horizon + 1, step):
        depart = depart_after + delta
        early_h, late_h, timeline, feasible = _simulate_route(stops, depart, speed)
        if not feasible:
            continue
        score = early_h * early_cost + late_h * late_cost
        if best is None or score < best['score'] - 1e-9:
            best = {'score': round(score, 4), 'depart_min': depart, 'early_h': round(early_h, 3),
                    'late_h': round(late_h, 3), 'timeline': timeline, 'feasible': True}
    if best is None:
        early_h, late_h, timeline, _ = _simulate_route(stops, depart_after, speed)
        best = {'score': round(early_h * early_cost + late_h * late_cost, 4), 'depart_min': depart_after,
                'early_h': round(early_h, 3), 'late_h': round(late_h, 3), 'timeline': timeline,
                'feasible': False}
    return best


def _number(params: Optional[Dict[str, str]], key: str, default: float) -> float:
    value = (params or {}).get(key)
    if value in (None, ''):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _single_customer_trip(customer: dict, vehicles: List[dict], dc: Tuple[float, float], speed: float,
                          depart_after: int, batch_code: str, index: int) -> dict:
    leg = haversine_km(dc[0], dc[1], customer['lon'], customer['lat'])
    back = haversine_km(customer['lon'], customer['lat'], dc[0], dc[1])
    distance = round_km(leg + back)
    service = float(customer.get('service_min') or 0)
    vehicle = _pick_vehicle(customer['total_kg'], customer['total_m3'], vehicles) or (vehicles[0] if vehicles else {})
    arrival = depart_after + _travel_min(leg, speed)
    return {
        'trip_id': f'{batch_code}-T{index}',
        'batch_id': batch_code,
        'product': customer['product_scope'],
        'customer_codes': [customer['code']],
        'stops': [{'customer': customer, 'leg_km': round_km(leg), 'arrival_min': round(arrival, 1),
                   'service_start_min': round(max(arrival, float(customer.get('exp_start') or arrival)), 1),
                   'service_end_min': round(max(arrival, float(customer.get('exp_start') or arrival)) + service, 1),
                   'vehicle': vehicle}],
        'load_kg': round(customer['total_kg'] or 0, 2),
        'volume_m3': round(customer['total_m3'] or 0, 3),
        'distance_km': distance,
        'duration_min': round(distance / speed * 60.0 + service, 1),
        'service_total_min': round(service, 1),
        'planned_departure_min': int(depart_after),
        'vehicle': vehicle,
        'capacity_kg': float(vehicle.get('capacity_kg') or 0),
        'capacity_m3': float(vehicle.get('capacity_m3') or 0),
        'load_rate': round((customer['total_kg'] or 0) / float(vehicle.get('capacity_kg') or 1) * 100, 1),
        'infeasible': True,
    }


# --------------------------------------------------------------------------
# 组装求解器输入
# --------------------------------------------------------------------------
def build_solver_data(trips: List[dict], vehicles: List[dict], params: Dict[str, str],
                      dc: Tuple[float, float], scope: str) -> dict:
    """把趟次组装成 ``solve_scenario`` 需要的 orders/trips/vehicles/docks/prep/charging。"""
    dock_count = int(params.get('_dock_count') or DOCK_DEFAULT_COUNT)
    docks = [{'dock_id': f'D{i + 1}', 'capacity': '1',
              'available_start': _hhmm(DOCK_OPEN_MIN), 'available_end': _hhmm(DOCK_CLOSE_MIN)}
             for i in range(dock_count)]

    route_cost = {}
    for trip in trips:
        vehicle = trip.get('vehicle') or {}
        fixed = float(vehicle.get('fixed_cost') or 0)
        unit = float(vehicle.get('unit_cost') or 0)
        route_cost[trip['trip_id']] = round(fixed + unit * float(trip['distance_km']), 2)

    solver_trips = []
    prep = []
    for trip in trips:
        load_min = max(10, int(round(trip['service_total_min'])))
        solver_trips.append({
            'trip_id': trip['trip_id'],
            'batch_id': trip['batch_id'],
            'product': trip['product'],
            'distance_km': str(trip['distance_km']),
            'duration_h': str(round(trip['duration_min'] / 60.0, 3)),
            'load_kg': str(int(round(float(trip['load_kg'])))),
            'load_h': str(round(load_min / 60.0, 3)),
            'planned_departure': _hhmm(trip['planned_departure_min']),
            'planned_departure_min': str(trip['planned_departure_min']),
            'route_cost_yuan': str(route_cost[trip['trip_id']]),
        })
        prep.append({'trip_id': trip['trip_id'], 'batch_id': trip['batch_id'],
                     'product': trip['product'], 'prep_min': str(PREP_MIN_DEFAULT), 'load_min': str(load_min)})

    solver_vehicles = []
    for vehicle in vehicles:
        energy = vehicle['energy_type']
        solver_vehicles.append({
            'vehicle_id': vehicle['code'],
            'vehicle_type': vehicle['vehicle_type'],
            'energy_type': energy,
            'capacity_kg': str(vehicle['capacity_kg']),
            'capacity_m3': str(vehicle['capacity_m3']),
            'available': '1',
            'km_cost': str(vehicle['unit_cost']),
            'fixed_cost': str(vehicle['fixed_cost']),
            'available_start': '01:00',
            'available_end': '12:00',
            'effective_range_km': str(vehicle.get('effective_range_km') or vehicle['range_km']),
        })

    charging = []
    for vehicle in vehicles:
        if vehicle['energy_type'] != '纯电':
            continue
        charging.append({'vehicle_id': vehicle['code'], 'charge_start': CHARGE_WINDOW[0],
                         'charge_end': _hhmm(_minutes(CHARGE_WINDOW[0]) + CHARGE_MINUTES),
                         'charger_id': 'C1'})

    served = [code for trip in trips for code in trip['customer_codes']]
    orders = [{'order_id': code, 'product': scope, 'weight_kg': ''} for code in served]
    return {'orders': orders, 'trips': solver_trips, 'vehicles': solver_vehicles,
            'docks': docks, 'prep': prep, 'charging': charging}


def _hhmm(minutes) -> str:
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def _minutes(text: str) -> int:
    hours, _, mins = str(text).partition(':')
    return int(hours) * 60 + int(mins or 0)


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def build_plan(conn: sqlite3.Connection, scope: str, fleet_tag: str,
               dc: Optional[Tuple[float, float]] = None, dock_count: Optional[int] = None) -> dict:
    """生成一次"客户→批次→趟次"方案（尚未求解装车时序）。"""
    params = load_params(conn)
    if dc is None:
        dc = (float(params.get('dc_lon', 104.26)), float(params.get('dc_lat', 30.85)))
    if dock_count:
        params['_dock_count'] = dock_count
    # 本品类各批次的最早发车时刻（"批次 N（HH:MM 起送）"）
    params['_batch_depart'] = {
        row['code']: row['depart_after']
        for row in conn.execute('SELECT code, depart_after FROM batches WHERE product_scope = ?', (scope,))
    }

    customers = load_customers(conn, scope)
    if not customers:
        raise PlanningError(f'{scope} 品类下没有可用客户，无法生成方案。')
    vehicles = load_vehicles(conn, fleet_tag)
    if not vehicles:
        raise PlanningError(f'车队 {fleet_tag} 下没有可用车辆，无法生成方案。')

    trips, warnings = build_trips(customers, vehicles, params, dc)
    assign_vehicles(trips, vehicles)          # 决定实际用几辆车、谁跑哪趟
    solver_data = build_solver_data(trips, vehicles, params, dc, scope)

    total_kg = sum(c['total_kg'] or 0 for c in customers)
    total_m3 = sum(c['total_m3'] or 0 for c in customers)
    served = {code for trip in trips for code in trip['customer_codes']}
    return {
        'scope': scope,
        'fleet_tag': fleet_tag,
        'dc': {'lon': dc[0], 'lat': dc[1]},
        'customers': [{
            'code': c['code'], 'name': c.get('name') or '', 'lon': c['lon'], 'lat': c['lat'],
            'batch_code': c.get('batch_code') or '', 'service_min': c.get('service_min') or 0,
            'exp_start': c.get('exp_start'), 'exp_end': c.get('exp_end'),
            'acc_start': c.get('acc_start'), 'acc_end': c.get('acc_end'),
            'total_kg': round(c['total_kg'] or 0, 2), 'total_m3': round(c['total_m3'] or 0, 3),
            'demands': [{'product_code': d['product_code'], 'demand_kg': d['demand_kg'],
                         'volume_m3': d['volume_m3']} for d in c['demands']],
        } for c in customers],
        'trips': [{
            'trip_id': t['trip_id'], 'batch_id': t['batch_id'], 'product': t['product'],
            'customer_codes': t['customer_codes'], 'sequence':
                [{'code': s['customer']['code'], 'leg_km': s['leg_km'],
                  'arrival_min': s['arrival_min'], 'service_start_min': s['service_start_min'],
                  'service_end_min': s['service_end_min']} for s in t['stops']],
            'load_kg': t['load_kg'], 'volume_m3': t['volume_m3'], 'distance_km': t['distance_km'],
            'duration_min': t['duration_min'], 'service_total_min': t['service_total_min'],
            'planned_departure_min': t['planned_departure_min'],
            'vehicle_code': (t.get('vehicle') or {}).get('code'),
            'vehicle_type': (t.get('vehicle') or {}).get('vehicle_type'),
            'energy_type': (t.get('vehicle') or {}).get('energy_type'),
            # 车辆成本随趟次带出，成本核算层不必再查库
            'fixed_cost': (t.get('vehicle') or {}).get('fixed_cost'),
            'unit_cost': (t.get('vehicle') or {}).get('unit_cost'),
            'maintenance_cost': (t.get('vehicle') or {}).get('maintenance_cost'),
            'capacity_kg': t['capacity_kg'], 'capacity_m3': t['capacity_m3'],
            'load_rate': t['load_rate'], 'infeasible': bool(t.get('infeasible')),
        } for t in trips],
        'vehicles': [{'code': v['code'], 'vehicle_type': v['vehicle_type'], 'energy_type': v['energy_type'],
                      'capacity_kg': v['capacity_kg'], 'capacity_m3': v['capacity_m3'],
                      'fixed_cost': v['fixed_cost'], 'unit_cost': v['unit_cost'],
                      'maintenance_cost': v.get('maintenance_cost'),
                      'effective_range_km': v.get('effective_range_km')} for v in vehicles],
        'summary': {
            'customers': len(customers),
            'served_customers': len(served),
            'trips': len(trips),
            'total_kg': round(total_kg, 1),
            'total_m3': round(total_m3, 3),
            'total_distance_km': round(sum(t['distance_km'] for t in trips), 2),
            'avg_load_rate': round(sum(t['load_rate'] for t in trips) / len(trips), 1) if trips else 0,
            'vehicles_used': len({(t.get('vehicle') or {}).get('code') for t in trips
                                  if (t.get('vehicle') or {}).get('code')}),
            'infeasible_trips': sum(1 for t in trips if t.get('infeasible')),
        },
        'warnings': warnings,
        'solver_data': solver_data,
    }


__all__ = [
    'CHARGE_WINDOW', 'DOCK_DEFAULT_COUNT', 'PlanningError', 'TURNAROUND_MIN', 'assign_vehicles',
    'build_plan', 'build_solver_data',
    'build_trips', 'load_customers', 'load_params', 'load_products', 'load_vehicles',
]
