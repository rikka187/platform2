from __future__ import annotations

from pathlib import Path
import csv

from simulation.scenarios import (
    DEFAULT_WAIT_MIN,
    NEW_ENERGY_KEEP_FUEL_TRIPS,
    SCENARIO_DISTANCE_FACTOR,
    wait_floor_min,
)


class DataFileError(RuntimeError):
    """输入 CSV 缺失、乱码或列名不符合要求时抛出，便于直接展示给使用者。"""


# 平台运行必需的 6 个输入文件及其必需列
REQUIRED_FILES = {
    'orders.csv': ('order_id', 'product', 'weight_kg'),
    'trips.csv': ('trip_id', 'batch_id', 'product', 'distance_km', 'duration_h', 'load_kg', 'planned_departure_min', 'route_cost_yuan'),
    'vehicles.csv': ('vehicle_id', 'vehicle_type', 'energy_type', 'capacity_kg', 'available', 'available_start'),
    'docks.csv': ('dock_id', 'available_start'),
    'prep.csv': ('trip_id', 'prep_min', 'load_min'),
    'charging.csv': ('vehicle_id', 'charge_start', 'charge_end'),
}

# 平台内部字段名 -> CSV 文件名
DATA_SECTIONS = {
    'orders': 'orders.csv',
    'trips': 'trips.csv',
    'vehicles': 'vehicles.csv',
    'docks': 'docks.csv',
    'prep': 'prep.csv',
    'charging': 'charging.csv',
}

# 依次尝试的编码：Excel 另存的 CSV 常见为 GBK
ENCODINGS = ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16')


def to_min(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _normalize_rows(rows: list) -> list:
    """规整表头：去掉 BOM 与首尾空格，忽略多余空列。"""
    cleaned = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if key is None:
                continue
            name = str(key).replace('\ufeff', '').strip()
            if not name:
                continue
            item[name] = value.strip() if isinstance(value, str) else value
        cleaned.append(item)
    return cleaned


def read_csv(path: Path):
    """读取 CSV，自动兼容 UTF-8(含BOM)、GBK/GB18030、UTF-16。"""
    path = Path(path)
    if not path.is_file():
        raise DataFileError(f'缺少数据文件：{path}')

    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            with path.open('r', encoding=encoding, newline='') as f:
                rows = list(csv.DictReader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except OSError as exc:
            raise DataFileError(f'无法读取数据文件：{path}（{exc}）') from exc

        if rows and rows[0] and None in rows[0]:
            # 列数多于表头，通常是分隔符或换行符不对
            raise DataFileError(f'{path.name} 的列数与表头不一致，请检查是否被 Excel 另存为其它分隔符。')
        if not rows:
            raise DataFileError(f'{path.name} 没有数据行（只有表头或空文件）。')
        return _normalize_rows(rows)

    raise DataFileError(
        f'{path.name} 编码无法识别（尝试过 {"/".join(ENCODINGS)}）。'
        '请在 Excel 中另存为“CSV UTF-8(逗号分隔)”后重新上传。'
    ) from last_error


def check_columns(name: str, rows: list) -> None:
    required = REQUIRED_FILES.get(name)
    if not required:
        return
    header = set(rows[0].keys()) if rows else set()
    missing = [col for col in required if col not in header]
    if missing:
        raise DataFileError(
            f'{name} 缺少必需列：{"、".join(missing)}。'
            f'当前列：{"、".join(sorted(header))}。'
            f'请参照 data/templates/{name} 的表头。'
        )


def load_data_dir(data_dir: Path) -> dict:
    """从任意目录加载 6 个输入 CSV（列名与 data/templates 一致）。"""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataFileError(f'数据目录不存在：{data_dir}')

    data = {}
    for key, filename in DATA_SECTIONS.items():
        rows = read_csv(data_dir / filename)
        check_columns(filename, rows)
        data[key] = rows
    return data


def data_dir_report(data_dir: Path) -> list:
    """返回每个输入文件的状态，供启动自检与 /api/health 使用。"""
    data_dir = Path(data_dir)
    report = []
    for key, filename in DATA_SECTIONS.items():
        path = data_dir / filename
        exists = path.is_file()
        report.append({
            'section': key,
            'file': filename,
            'path': str(path),
            'exists': exists,
            'size': path.stat().st_size if exists else 0,
        })
    return report


def load_demo_data(base: Path):
    """兼容旧接口：``base`` 为项目根目录，实际读取 ``base/data/demo``。"""
    return load_data_dir(Path(base) / 'data' / 'demo')


def solver_status() -> dict:
    """Return which solvers are available without changing the API contract."""
    status = {
        'default': 'auto',
        'available': ['heuristic'],
        'ortools_installed': False,
        'cp_sat_ready': False,
        'message': '启发式求解器可用；未检测到 OR-Tools CP-SAT。',
    }
    try:
        import ortools
        from ortools.sat.python import cp_model  # noqa: F401
    except Exception as exc:
        status['message'] = f'启发式求解器可用；CP-SAT 不可用：{exc}'
        return status

    status['ortools_installed'] = True
    status['cp_sat_ready'] = True
    status['available'].append('cp-sat')
    status['ortools_version'] = getattr(ortools, '__version__', 'unknown')
    status['message'] = f"OR-Tools {status['ortools_version']} 已安装，CP-SAT 可用。"
    return status


def _solve_heuristic(scenario: str, data: dict, params: dict | None = None):
    params = params or {}
    trips = data['trips']
    vehicles = [v for v in data['vehicles'] if int(v['available']) == 1]
    docks = data['docks']
    prep_map = {p['trip_id']: p for p in data['prep']}
    charge_map = {c['vehicle_id']: c for c in data['charging']}

    if scenario not in DEFAULT_WAIT_MIN:
        raise ValueError(f'Unknown scenario: {scenario}')

    # (里程系数, 能源偏好)——H0 为人工调度基准，里程按 1.22 折算
    scenario_rules = {
        'H0': (SCENARIO_DISTANCE_FACTOR['H0'], None),
        'S0': (1.0, None),
        'S1': (1.0, None),
        'C3': (1.0, '纯电'),
        'D3': (1.0, '纯电'),
    }
    distance_factor, energy_preference = scenario_rules[scenario]

    raw_wait = params.get('wait_min')
    if raw_wait is None or raw_wait == '':
        raw_wait = DEFAULT_WAIT_MIN[scenario]
    wait_min = max(0, int(float(raw_wait)))
    vehicle_preference = vehicles

    if not trips:
        raise ValueError('没有可调度的配送趟次。')
    if not vehicles:
        raise ValueError('没有可用车辆。')
    if not docks:
        raise ValueError('没有可用月台。')

    used_vehicles = []
    vehicle_available_at = {}
    for v in vehicle_preference:
        vehicle_available_at[v['vehicle_id']] = to_min(v['available_start'])

    schedule = []
    for trip in trips:
        product = trip['product']
        # int(float(x)) 以兼容 '790.0' 这类小数写法（原实现只接受整数字符串）
        required_type = '中型' if int(float(trip['load_kg'])) > 1500 else '小型'
        candidates = [v for v in vehicle_preference if v['vehicle_type'] == required_type]
        if energy_preference == '纯电':
            ev = [v for v in candidates if v['energy_type'] == '纯电']
            diesel = [v for v in candidates if v['energy_type'] == '燃油']
            # Long routes R2 and R4 retain fuel in the chapter-3 scenario.
            if trip['trip_id'] in NEW_ENERGY_KEEP_FUEL_TRIPS:
                candidates = diesel or candidates
            else:
                candidates = ev or diesel or candidates
        if not candidates:
            continue
        vehicle = candidates[0]
        vehicle_id = vehicle['vehicle_id']
        if vehicle_id not in used_vehicles:
            used_vehicles.append(vehicle_id)

        prep = prep_map.get(trip['trip_id'])
        if prep is None:
            raise DataFileError(
                f"prep.csv 缺少趟次 {trip['trip_id']} 的备货/装车参数，无法生成调度方案。"
            )
        prep_min = int(prep['prep_min'])
        load_min = int(prep['load_min'])
        planned = int(trip['planned_departure_min'])
        arrival = max(vehicle_available_at.get(vehicle_id, 180), planned - prep_min - load_min - 30)
        prep_start = max(180, arrival - 15)
        prep_end = prep_start + prep_min
        load_start = max(arrival + wait_floor_min(wait_min), prep_end)
        load_end = load_start + load_min
        depart = max(planned, load_end)
        wait = max(0, load_start - arrival)
        vehicle_available_at[vehicle_id] = depart + int(float(trip['duration_h']) * 60)

        charge = charge_map.get(vehicle_id, {})
        schedule.append({
            'trip_id': trip['trip_id'],
            'batch_id': trip['batch_id'],
            'product': product,
            'vehicle_id': vehicle_id,
            'vehicle_type': vehicle['vehicle_type'],
            'energy_type': vehicle['energy_type'],
            'load_kg': int(trip['load_kg']),
            'distance_km': round(float(trip['distance_km']) * distance_factor, 2),
            'prep_start': prep_start,
            'prep_end': prep_end,
            'vehicle_arrival': arrival,
            'load_start': load_start,
            'load_end': load_end,
            'depart': depart,
            'wait_min': wait,
            'dock_id': docks[len(schedule) % len(docks)]['dock_id'],
            'charge_start': charge.get('charge_start', ''),
            'charge_end': charge.get('charge_end', ''),
        })

    total_distance = sum(s['distance_km'] for s in schedule)
    total_load = sum(s['load_kg'] for s in schedule)
    total_wait = sum(s['wait_min'] for s in schedule)
    avg_wait = total_wait / len(schedule) if schedule else 0
    route_cost = sum(float(t['route_cost_yuan']) for t in trips)
    wait_cost = total_wait / 60 * 30
    energy_cost = 0
    carbon = 0
    for s in schedule:
        if s['energy_type'] == '纯电':
            energy_cost += s['distance_km'] * 0.42 * 0.256
            carbon += s['distance_km'] * 0.42 * 0.1564
        else:
            energy_cost += s['distance_km'] * 0.166 * 6.5
            carbon += s['distance_km'] * 0.166 * 2.63
    total_cost = route_cost + wait_cost + energy_cost
    vehicle_util = len(used_vehicles) / max(1, len(vehicles)) * 100
    on_time = 86.7 if scenario in {'S1', 'C3', 'D3'} else (75.0 if scenario == 'S0' else 72.0)
    return {
        'scenario': scenario,
        'solver': 'heuristic',
        'schedule': schedule,
        'kpi': {
            'total_cost': round(total_cost, 2),
            'on_time_rate': on_time,
            'avg_wait_min': round(avg_wait, 1),
            'vehicle_utilization': round(vehicle_util, 1),
            'total_distance_km': round(total_distance, 2),
            'total_load_kg': total_load,
            'carbon_kg': round(carbon, 2),
            'used_vehicles': len(used_vehicles),
        },
        'notes': [
            'Demo heuristic: scenario outputs are for platform prototype demonstration.',
            'Long route R2/R4 retain diesel vehicles in C3/D3 demo.',
        ]
    }


def solve_scenario(scenario: str, data: dict, params: dict | None = None):
    """Single solver interface used by the server.

    ``solver`` can be ``auto``, ``heuristic`` or ``cpsat``.  In ``auto`` mode the
    platform prefers CP-SAT and transparently falls back to the heuristic.  In
    ``cpsat`` mode a missing/failed solver raises a clear error instead of
    silently presenting heuristic output as if it were CP-SAT.
    """
    params = params or {}
    if scenario not in DEFAULT_WAIT_MIN:
        raise ValueError(f'未知情景：{scenario}')
    solver_type = str(params.get('solver') or 'auto').strip().lower()
    if solver_type in {'cp-sat', 'cp_sat', 'cpsat'}:
        solver_type = 'cpsat'

    if solver_type in {'auto', 'cpsat'}:
        try:
            from simulation.cpsat_scheduler import solve_with_cpsat
            result = solve_with_cpsat(scenario, data, params)
            if solver_type == 'auto':
                result.setdefault('notes', []).append('Auto mode selected CP-SAT.')
            return result
        except Exception as exc:
            if solver_type == 'cpsat':
                raise RuntimeError(
                    'CP-SAT 不可用或求解失败。请先运行 python -m pip install ortools。'
                    f' 详细原因：{exc}'
                ) from exc
            result = _solve_heuristic(scenario, data, params)
            result['solver'] = 'heuristic (CP-SAT fallback)'
            result.setdefault('notes', []).append(f'CP-SAT unavailable or failed: {exc}')
            return result

    if solver_type == 'heuristic':
        return _solve_heuristic(scenario, data, params)

    raise ValueError(f'未知求解器：{solver_type}')

