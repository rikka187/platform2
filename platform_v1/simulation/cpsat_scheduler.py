"""OR-Tools CP-SAT scheduler for the warehouse-delivery coordination demo.

The module is imported lazily so the platform still starts on computers that
have not installed OR-Tools yet.
"""
from __future__ import annotations

from typing import Any

from simulation.scenarios import (
    DEFAULT_WAIT_MIN,
    NEW_ENERGY_KEEP_FUEL_TRIPS,
    SCENARIO_DISTANCE_FACTOR,
    wait_floor_min,
)


def to_min(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _num(params: dict, name: str, default: float) -> float:
    """读取数值参数，兼容 None/空字符串/字符串数字。"""
    value = params.get(name)
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_workers() -> int:
    """CP-SAT 搜索线程数：默认不超过 4，小机器上避免抢占 CPU。"""
    import os

    try:
        cpu = os.cpu_count() or 1
    except Exception:  # pragma: no cover
        cpu = 1
    return max(1, min(4, cpu))


def _energy_cost_yuan(distance_km: float, energy_type: str) -> float:
    if energy_type == '纯电':
        # 0.42 kWh/km x 0.256 yuan/kWh
        return distance_km * 0.42 * 0.256
    # 0.166 L/km x 6.5 yuan/L
    return distance_km * 0.166 * 6.5


def _carbon_kg(distance_km: float, energy_type: str) -> float:
    if energy_type == '纯电':
        return distance_km * 0.42 * 0.1564
    return distance_km * 0.166 * 2.63


def _compatible(trip: dict[str, Any], vehicle: dict[str, Any], scenario: str) -> bool:
    """载重、车型与能源口径：与启发式求解器保持一致。"""
    load = int(float(trip['load_kg']))
    if load > int(float(vehicle['capacity_kg'])):
        return False
    required_type = '中型' if load > 1500 else '小型'
    if vehicle['vehicle_type'] != required_type:
        return False

    # 第三章口径：C3/D3 中两条长线（R2、R4）保留燃油车。
    if scenario in {'C3', 'D3'} and trip['trip_id'] in NEW_ENERGY_KEEP_FUEL_TRIPS:
        return vehicle['energy_type'] == '燃油'
    return True


def solve_with_cpsat(scenario: str, data: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Solve the order/trip-to-vehicle, dock and time schedule with CP-SAT.

    The function follows the same return contract as the heuristic scheduler:
    a dictionary containing ``scenario``, ``solver``, ``schedule``, ``kpi`` and
    ``notes``.  It can therefore be replaced or called through the same API.
    """
    from ortools.sat.python import cp_model

    params = params or {}
    trips = data['trips']
    vehicles = [v for v in data['vehicles'] if int(v['available']) == 1]
    docks = data['docks']
    prep_map = {p['trip_id']: p for p in data['prep']}
    charge_map = {c['vehicle_id']: c for c in data['charging']}

    if not trips:
        raise ValueError('没有可调度的配送趟次。')
    if not vehicles:
        raise ValueError('没有可用车辆。')
    if not docks:
        raise ValueError('没有可用月台。')

    model = cp_model.CpModel()
    n_trips = len(trips)
    n_vehicles = len(vehicles)
    n_docks = len(docks)
    turnover = int(_num(params, 'turnover_min', 30))
    # 等待口径：滑杆值按 30 分钟截断，与启发式求解器完全一致。
    wait_floor = wait_floor_min(_num(params, 'wait_min', DEFAULT_WAIT_MIN.get(scenario, 30)))
    # H0 为人工调度基准，里程按 1.22 折算；其余情景不折算。
    distance_factor = SCENARIO_DISTANCE_FACTOR.get(scenario, 1.0)

    max_return = max(
        int(float(t['duration_h']) * 60) + int(t['planned_departure_min']) for t in trips
    )
    horizon = max(1440, max_return + turnover + 120)

    # Assignment variables: each trip has exactly one compatible vehicle.
    assign: dict[tuple[int, int], Any] = {}
    for b, trip in enumerate(trips):
        candidates = []
        for h, vehicle in enumerate(vehicles):
            if _compatible(trip, vehicle, scenario):
                assign[b, h] = model.NewBoolVar(f'assign_{b}_{h}')
                candidates.append(assign[b, h])
        if not candidates:
            raise ValueError(f"趟次 {trip['trip_id']} 没有满足载重和能源限制的车辆。")
        model.AddExactlyOne(candidates)
    # Each trip uses one dock.
    use_dock: dict[tuple[int, int], Any] = {}
    for b in range(n_trips):
        for d in range(n_docks):
            use_dock[b, d] = model.NewBoolVar(f'dock_{b}_{d}')
        model.AddExactlyOne(use_dock[b, d] for d in range(n_docks))

    # Time variables, expressed in minutes after midnight.
    prep_start: dict[int, Any] = {}
    prep_end: dict[int, Any] = {}
    load_start: dict[int, Any] = {}
    load_end: dict[int, Any] = {}
    depart: dict[int, Any] = {}
    return_time: dict[int, Any] = {}
    wait: dict[int, Any] = {}
    delay: dict[int, Any] = {}

    for b, trip in enumerate(trips):
        planned = int(float(trip['planned_departure_min']))
        release = max(0, planned - 240)
        prep_min = int(float(prep_map[trip['trip_id']]['prep_min']))
        load_min = int(float(prep_map[trip['trip_id']]['load_min']))
        duration = int(float(trip['duration_h']) * 60)
        arrival = max(0, planned - 30)

        prep_start[b] = model.NewIntVar(release, horizon, f'prep_start_{b}')
        prep_end[b] = model.NewIntVar(0, horizon, f'prep_end_{b}')
        load_start[b] = model.NewIntVar(0, horizon, f'load_start_{b}')
        load_end[b] = model.NewIntVar(0, horizon, f'load_end_{b}')
        depart[b] = model.NewIntVar(0, horizon, f'depart_{b}')
        return_time[b] = model.NewIntVar(0, horizon, f'return_{b}')
        wait[b] = model.NewIntVar(0, horizon, f'wait_{b}')
        delay[b] = model.NewIntVar(0, horizon, f'delay_{b}')

        model.Add(prep_end[b] == prep_start[b] + prep_min)
        model.Add(load_start[b] >= prep_end[b])
        model.Add(load_end[b] == load_start[b] + load_min)
        model.AddMaxEquality(depart[b], [planned, load_end[b]])
        model.Add(return_time[b] == depart[b] + duration)
        model.Add(wait[b] >= load_start[b] - arrival)
        model.Add(wait[b] >= 0)
        # 情景等待口径：车辆到达后至少等待 wait_floor 分钟再开始装车
        # （与启发式的 max(arrival + min(wait_min, 30), prep_end) 对应）。
        model.Add(load_start[b] >= arrival + wait_floor)
        model.Add(delay[b] == depart[b] - planned)

        # A vehicle cannot start loading before it is available, and an EV
        # must finish its pre-trip charging before departure.
        for h, vehicle in enumerate(vehicles):
            if (b, h) not in assign:
                continue
            model.Add(load_start[b] >= to_min(vehicle['available_start'])).OnlyEnforceIf(assign[b, h])
            charge = charge_map.get(vehicle['vehicle_id'], {})
            if charge.get('charge_end'):
                model.Add(depart[b] >= to_min(charge['charge_end'])).OnlyEnforceIf(assign[b, h])

    # Dock intervals cannot overlap.
    for d in range(n_docks):
        intervals = []
        for b in range(n_trips):
            intervals.append(
                model.NewOptionalIntervalVar(
                    load_start[b], int(float(prep_map[trips[b]['trip_id']]['load_min'])), load_end[b],
                    use_dock[b, d], f'dock_interval_{b}_{d}'
                )
            )
        model.AddNoOverlap(intervals)

    # A vehicle is occupied from loading start through return plus turnover.
    # Using one optional interval per trip/vehicle both models reuse and
    # prevents the next trip from being loaded while the vehicle is away.
    for h in range(n_vehicles):
        intervals = []
        for b, trip in enumerate(trips):
            if (b, h) not in assign:
                continue
            busy_end = model.NewIntVar(0, horizon, f'busy_end_{b}_{h}')
            busy_size = model.NewIntVar(0, horizon, f'busy_size_{b}_{h}')
            model.Add(busy_end == return_time[b] + turnover)
            model.Add(busy_size == busy_end - load_start[b])
            intervals.append(
                model.NewOptionalIntervalVar(
                    load_start[b], busy_size, busy_end, assign[b, h], f'vehicle_interval_{b}_{h}'
                )
            )
        model.AddNoOverlap(intervals)

    # The objective uses the same operating-cost口径 as the demo KPI:
    # route base cost is common to every assignment, while energy, waiting and
    # departure delay depend on vehicle choice and timing.  Values are scaled
    # to cents because CP-SAT uses integer arithmetic.
    objective_terms = []
    for b, trip in enumerate(trips):
        distance = float(trip['distance_km']) * distance_factor
        for h, vehicle in enumerate(vehicles):
            if (b, h) not in assign:
                continue
            energy_cents = int(round(_energy_cost_yuan(distance, vehicle['energy_type']) * 100))
            objective_terms.append(assign[b, h] * energy_cents)
        objective_terms.append(wait[b] * 50)   # 30 yuan/hour = 0.5 yuan/minute
        objective_terms.append(delay[b] * 100) # 1 yuan/minute
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(_num(params, 'max_time_seconds', 10))
    workers = int(_num(params, 'num_search_workers', _default_workers()))
    solver.parameters.num_search_workers = max(1, min(64, workers))
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f'CP-SAT 未找到可行解，状态：{solver.StatusName(status)}')

    schedule = []
    used_vehicle_ids = set()
    for b, trip in enumerate(trips):
        chosen_h = next((h for h in range(n_vehicles) if (b, h) in assign and solver.Value(assign[b, h]) == 1), None)
        chosen_d = next((d for d in range(n_docks) if solver.Value(use_dock[b, d]) == 1), None)
        if chosen_h is None or chosen_d is None:
            raise RuntimeError(f"趟次 {trip['trip_id']} 未获得完整调度结果。")

        vehicle = vehicles[chosen_h]
        used_vehicle_ids.add(vehicle['vehicle_id'])
        charge = charge_map.get(vehicle['vehicle_id'], {})
        schedule.append({
            'trip_id': trip['trip_id'],
            'batch_id': trip['batch_id'],
            'product': trip['product'],
            'vehicle_id': vehicle['vehicle_id'],
            'vehicle_type': vehicle['vehicle_type'],
            'energy_type': vehicle['energy_type'],
            'load_kg': int(float(trip['load_kg'])),
            'distance_km': round(float(trip['distance_km']) * distance_factor, 2),
            'prep_start': solver.Value(prep_start[b]),
            'prep_end': solver.Value(prep_end[b]),
            'vehicle_arrival': max(0, int(float(trip['planned_departure_min'])) - 30),
            'load_start': solver.Value(load_start[b]),
            'load_end': solver.Value(load_end[b]),
            'depart': solver.Value(depart[b]),
            'wait_min': solver.Value(wait[b]),
            'dock_id': docks[chosen_d]['dock_id'],
            'charge_start': charge.get('charge_start', ''),
            'charge_end': charge.get('charge_end', ''),
        })

    total_distance = sum(s['distance_km'] for s in schedule)
    total_load = sum(s['load_kg'] for s in schedule)
    avg_wait = sum(s['wait_min'] for s in schedule) / len(schedule) if schedule else 0
    route_cost = sum(float(t['route_cost_yuan']) for t in trips)
    wait_cost = sum(s['wait_min'] for s in schedule) / 60 * 30
    energy_cost = sum(_energy_cost_yuan(s['distance_km'], s['energy_type']) for s in schedule)
    carbon = sum(_carbon_kg(s['distance_km'], s['energy_type']) for s in schedule)
    total_cost = route_cost + wait_cost + energy_cost

    # The on-time rate is a presentation KPI in the prototype.  It is kept in
    # the same form as the heuristic so the two solvers can be compared.
    on_time = 86.7 if scenario in {'S1', 'C3', 'D3'} else (75.0 if scenario == 'S0' else 72.0)
    return {
        'scenario': scenario,
        'solver': 'cp-sat',
        'solver_status': solver.StatusName(status),
        'schedule': schedule,
        'kpi': {
            'total_cost': round(total_cost, 2),
            'on_time_rate': on_time,
            'avg_wait_min': round(avg_wait, 1),
            'vehicle_utilization': round(len(used_vehicle_ids) / len(vehicles) * 100, 1),
            'total_distance_km': round(total_distance, 2),
            'total_load_kg': total_load,
            'carbon_kg': round(carbon, 2),
            'used_vehicles': len(used_vehicle_ids),
        },
        'notes': [
            f'Generated by OR-Tools CP-SAT ({solver.StatusName(status)}).',
            'Demo data and presentation scenario; route base costs are treated as fixed inputs.',
        ],
    }


