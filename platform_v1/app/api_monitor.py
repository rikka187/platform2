"""监控反馈：车辆位置/温度/电量/月台占用的模拟流 + 四类状态与预警分级。

设计要点
--------
* **确定性模拟**：位置与传感器读数由 (趟次, 里程进度, tick) 经 md5 派生，
  同一 tick 永远得到同一份快照，便于测试与回溯（不使用随机数）；
* **四类状态**：正常 / 临界 / 异常 / 严重异常；
* **预警分级**：一级提示、二级警告、三级严重，并给出建议处置；
* **处置留痕**：确认/处置写入 ``events`` 表并记审计。

监控对象来自最近一次（或指定）方案的趟次与装车时序：
车辆位置沿"配送中心 → 客户 → 配送中心"折线按时间插值；
温度按品类基准 + 小幅波动；纯电按耗电率降 SOC，燃油按油耗降油量。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Dict, List, Optional

from app import db
from app.api_admin import ApiProblem, _current_user
from app.auth import require
from app.http import Response
from simulation.geo import haversine_km

# 品类温控基准（℃）与冷链上限
TEMP_BASE = {'姜蒜': 8.0, '鲜面条': 4.0}
TEMP_LIMIT = {'姜蒜': 12.0, '鲜面条': 7.0}
STATUS_ORDER = ['正常', '临界', '异常', '严重异常']
STATUS_LEVEL = {'正常': 0, '临界': 1, '异常': 2, '严重异常': 3}
ALERT_LEVEL = {1: '一级提示', 2: '二级警告', 3: '三级严重'}


def _noise(seed: str, scale: float) -> float:
    """由字符串派生的确定性"噪声"，范围 [-scale, scale]。"""
    digest = hashlib.md5(seed.encode('utf-8')).hexdigest()
    value = int(digest[:8], 16) / 0xFFFFFFFF          # 0..1
    return (value * 2 - 1) * scale


def _worst(statuses: List[str]) -> str:
    if not statuses:
        return '正常'
    return max(statuses, key=lambda name: STATUS_LEVEL.get(name, 0))


def _grade(level: int, metric: str, detail: str, suggestion: str,
           objects: List[str]) -> dict:
    return {'level': level, 'level_text': ALERT_LEVEL[level], 'metric': metric,
            'detail': detail, 'suggestion': suggestion, 'objects': objects}


def _load_run(conn: sqlite3.Connection, run_id: Optional[int]) -> dict:
    row = None
    if run_id:
        row = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    else:
        row = conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    if not row:
        raise ApiProblem('还没有任何方案，请先在「路径与方案」生成一次方案。', status=404)
    payload = {}
    try:
        payload = json.loads(row['plan_json'] or '{}')
    except json.JSONDecodeError:
        payload = {}
    return {'run_id': row['id'], 'scenario': row['scenario'], 'scope': row['scope'],
            'fleet_tag': row['fleet_tag'], 'plan': payload.get('plan') or {},
            'solution': payload.get('solution') or {}}


def _route_points(plan: dict, trip: dict) -> List[dict]:
    """趟次的折线：配送中心 → 各客户 → 配送中心，带累计里程。"""
    by_code = {str(c['code']): c for c in plan.get('customers', [])}
    dc = plan.get('dc') or {'lon': 104.26, 'lat': 30.85}
    points = [{'role': 'dc', 'code': '0', 'lon': dc['lon'], 'lat': dc['lat'], 'km': 0.0}]
    total = 0.0
    previous = (dc['lon'], dc['lat'])
    for code in trip.get('customer_codes', []):
        customer = by_code.get(str(code))
        if not customer:
            continue
        total += haversine_km(previous[0], previous[1], customer['lon'], customer['lat'])
        points.append({'role': 'customer', 'code': str(code), 'lon': customer['lon'],
                       'lat': customer['lat'], 'km': round(total, 2)})
        previous = (customer['lon'], customer['lat'])
    total += haversine_km(previous[0], previous[1], dc['lon'], dc['lat'])
    points.append({'role': 'dc', 'code': '0', 'lon': dc['lon'], 'lat': dc['lat'], 'km': round(total, 2)})
    return points


def _position_at(points: List[dict], distance_km: float) -> dict:
    """按已行驶里程在折线上插值出车辆位置。"""
    if not points:
        return {'lon': None, 'lat': None, 'progress': 0.0, 'next': None}
    total = points[-1]['km'] or 1.0
    distance_km = max(0.0, min(distance_km, total))
    for start, end in zip(points, points[1:]):
        if distance_km <= end['km'] or end is points[-1]:
            span = max(1e-6, end['km'] - start['km'])
            ratio = (distance_km - start['km']) / span
            return {
                'lon': round(start['lon'] + (end['lon'] - start['lon']) * ratio, 5),
                'lat': round(start['lat'] + (end['lat'] - start['lat']) * ratio, 5),
                'progress': round(distance_km / total * 100, 1),
                'next': end['code'] if end['role'] == 'customer' else '返场',
                'leg_km': round(distance_km - start['km'], 2),
            }
    return {'lon': points[-1]['lon'], 'lat': points[-1]['lat'], 'progress': 100.0, 'next': '返场'}


def snapshot(conn: sqlite3.Connection, run_id: Optional[int], tick: int) -> dict:
    """生成 ``tick`` 时刻（当日分钟数）的监控快照。"""
    data = _load_run(conn, run_id)
    plan = data['plan']
    solution = data['solution']
    scope = data.get('scope') or '姜蒜'
    trip_map = {str(t['trip_id']): t for t in plan.get('trips', [])}
    schedule = solution.get('schedule') or []
    if not schedule:
        raise ApiProblem('该方案没有装车时序，无法模拟监控数据。', status=400)

    base_temp = TEMP_BASE.get(scope, 6.0)
    limit_temp = TEMP_LIMIT.get(scope, 10.0)
    by_code = {str(c['code']): c for c in plan.get('customers', [])}
    vehicles: List[dict] = []
    alerts: List[dict] = []
    trip_sequence: Dict[str, int] = {}      # 同一辆车的第几趟（影响出发电量）

    for entry in schedule:
        trip = trip_map.get(str(entry.get('trip_id'))) or {}
        code = str(entry.get('vehicle_id'))
        points = _route_points(plan, trip)
        depart = int(entry.get('depart') or 0)
        duration = float(trip.get('duration_min') or 60)
        elapsed = tick - depart
        distance_total = float(trip.get('distance_km') or 0)
        status = '正常'
        notes: List[dict] = []

        if elapsed < 0:
            phase = '待发车'
            travelled = 0.0
        elif elapsed >= duration:
            phase = '已完成'
            travelled = distance_total
        else:
            phase = '配送中'
            travelled = distance_total * (elapsed / max(1.0, duration))
        position = _position_at(points, travelled)

        # 温度：基准 + 确定性波动；开门卸货时段波动更大
        unloading = phase == '配送中' and 0 < (elapsed % 25) < 6
        temp = base_temp + _noise(f'{code}:{tick}:temp', 1.2) + (1.6 if unloading else 0.0)
        temp = round(temp, 1)
        if temp > limit_temp + 3:
            status = _worst([status, '严重异常'])
            notes.append(_grade(3, '温度', f'{code} 厢温 {temp}℃ 超过限值 {limit_temp}℃ 达 {round(temp - limit_temp, 1)}℃',
                                '立即检查冷机与厢门密封，必要时改派就近车辆', [entry.get('trip_id')]))
        elif temp > limit_temp:
            status = _worst([status, '异常'])
            notes.append(_grade(2, '温度', f'{code} 厢温 {temp}℃ 高于限值 {limit_temp}℃',
                                '联系司机确认冷机状态并记录', [entry.get('trip_id')]))
        elif temp > limit_temp - 1:
            status = _worst([status, '临界'])
            notes.append(_grade(1, '温度', f'{code} 厢温 {temp}℃ 接近限值',
                                '加强巡检频次', [entry.get('trip_id')]))

        # 电量/油量：纯电按 0.42 kWh/km（小型口径）折算 SOC，燃油按 11.8 L/100km 折算。
        # 连续趟次的车辆出发电量递减（回场后来不及充满）。
        energy_type = (trip.get('energy_type') or '燃油')
        sequence = trip_sequence.get(code, 0)
        trip_sequence[code] = sequence + 1
        if energy_type == '纯电':
            battery_capacity = 85.0
            soc_start = max(35.0, 80.0 - sequence * 15.0)
            used = travelled * 0.42 / battery_capacity * 100
            soc = round(max(0.0, soc_start - used), 1)
            energy = {'type': '纯电', 'value': soc, 'unit': '%'}
            if soc < 15:
                status = _worst([status, '严重异常'])
                notes.append(_grade(3, '电量', f'{code} 剩余电量 {soc}%', '就近安排补电或换成燃油车', [entry.get('trip_id')]))
            elif soc < 30:
                status = _worst([status, '异常'])
                notes.append(_grade(2, '电量', f'{code} 剩余电量 {soc}%', '提醒司机回场后补电', [entry.get('trip_id')]))
            elif soc < 45:
                status = _worst([status, '临界'])
                notes.append(_grade(1, '电量', f'{code} 剩余电量 {soc}%', '纳入回场补电清单', [entry.get('trip_id')]))
        else:
            fuel_capacity = 100.0
            used = travelled * 0.118
            fuel = round(max(0.0, fuel_capacity - used), 1)
            energy = {'type': '燃油', 'value': fuel, 'unit': 'L'}
            if fuel < 15:
                status = _worst([status, '异常'])
                notes.append(_grade(2, '油量', f'{code} 剩余油量 {fuel} L', '回场加油', [entry.get('trip_id')]))

        # 在途延误：由 (车辆, tick) 派生的确定性路况波动（±18 分钟）
        delay_min = 0
        if phase == '配送中':
            delay_min = int(round(_noise(f'{code}:{tick}:traffic', 18)))
            if delay_min >= 30:
                status = _worst([status, '严重异常'])
                notes.append(_grade(3, '延误', f'{code} 较计划延误 {delay_min} 分钟',
                                    '立即联系司机核实，必要时调整后续客户顺序', [entry.get('trip_id')]))
            elif delay_min >= 15:
                status = _worst([status, '异常'])
                notes.append(_grade(2, '延误', f'{code} 较计划延误 {delay_min} 分钟',
                                    '通知下游客户并预留时间', [entry.get('trip_id')]))
            elif delay_min >= 8:
                status = _worst([status, '临界'])
                notes.append(_grade(1, '延误', f'{code} 较计划慢 {delay_min} 分钟',
                                    '持续关注，超阈值再升级', [entry.get('trip_id')]))

        vehicles.append({
            'vehicle_code': code,
            'trip_id': entry.get('trip_id'),
            'trip_code': trip.get('trip_id'),
            'dock_code': entry.get('dock_id'),
            'energy_type': energy_type,
            'phase': phase,
            'tick': tick,
            'depart': depart,
            'elapsed_min': int(elapsed),
            'delay_min': delay_min,
            'lon': position['lon'],
            'lat': position['lat'],
            'progress': position['progress'],
            'next_stop': position.get('next'),
            'travelled_km': round(travelled, 2),
            'distance_km': round(distance_total, 2),
            'temperature': temp,
            'temperature_limit': limit_temp,
            'energy': energy,
            'status': status,
            'status_level': STATUS_LEVEL[status],
            'alerts': notes,
            'customer_codes': trip.get('customer_codes') or [],
            'load_kg': trip.get('load_kg'),
        })
        alerts.extend(notes)

    # 月台占用
    docks = []
    for entry in schedule:
        start, end = entry.get('load_start'), entry.get('load_end')
        if start is None or end is None:
            continue
        occupied = int(start) <= tick <= int(end)
        docks.append({'dock_code': entry.get('dock_id'), 'trip_id': entry.get('trip_id'),
                      'vehicle_code': entry.get('vehicle_id'), 'occupied': occupied,
                      'load_start': int(start), 'load_end': int(end),
                      'queue': 0})
    queues: Dict[str, int] = {}
    for dock in docks:
        if not dock['occupied'] and dock['load_start'] > tick:
            queues[dock['dock_code']] = queues.get(dock['dock_code'], 0) + 1
    for dock in docks:
        dock['queue'] = queues.get(dock['dock_code'], 0)
    occupied_count = sum(1 for dock in docks if dock['occupied'])

    alert_summary = {ALERT_LEVEL[level]: sum(1 for a in alerts if a['level'] == level) for level in (1, 2, 3)}
    status_summary = {name: sum(1 for v in vehicles if v['status'] == name) for name in STATUS_ORDER}
    return {
        'run_id': data['run_id'],
        'scenario': data['scenario'],
        'scope': scope,
        'fleet_tag': data.get('fleet_tag'),
        'tick': tick,
        'dc': plan.get('dc'),
        'customers': plan.get('customers', []),
        'vehicles': vehicles,
        'docks': docks,
        'alerts': alerts,
        'summary': {
            'vehicles': len(vehicles),
            'in_transit': sum(1 for v in vehicles if v['phase'] == '配送中'),
            'finished': sum(1 for v in vehicles if v['phase'] == '已完成'),
            'waiting': sum(1 for v in vehicles if v['phase'] == '待发车'),
            'docks_total': len(docks),
            'docks_occupied': occupied_count,
            'docks_idle': len(docks) - occupied_count,
            'status': status_summary,
            'alerts': alert_summary,
            'worst_status': _worst([v['status'] for v in vehicles]),
        },
    }


def _events(conn: sqlite3.Connection, run_id: Optional[int], limit: int) -> List[dict]:
    sql = 'SELECT * FROM events WHERE 1=1'
    args: List = []
    if run_id:
        sql += ' AND run_id = ?'
        args.append(run_id)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    return [dict(row) for row in conn.execute(sql, args)]


def current_state(conn: sqlite3.Connection, query: dict) -> Response:
    raw_run = (query.get('run_id') or [''])[0]
    run_id = int(raw_run) if str(raw_run).isdigit() else None
    raw_tick = (query.get('tick') or [''])[0]
    tick = int(raw_tick) if str(raw_tick).isdigit() else 8 * 60
    snap = snapshot(conn, run_id, tick)
    snap['events'] = _events(conn, snap['run_id'], 20)
    return Response(200, snap)


def acknowledge(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    run_id = payload.get('run_id')
    run_id = int(run_id) if str(run_id or '').isdigit() else None
    level = int(payload.get('level') or 1)
    metric = str(payload.get('metric') or '')
    objects = payload.get('objects') or []
    if isinstance(objects, list):
        objects = ','.join(str(x) for x in objects)
    action = str(payload.get('action') or '').strip()
    if action not in ('确认', '处置', '忽略'):
        raise ApiProblem('处置动作只能是 确认 / 处置 / 忽略。')
    note = str(payload.get('note') or '').strip()
    conn.execute(
        '''INSERT INTO events (ts, run_id, level, level_text, metric, object_codes, action, note,
               created_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (db.now(), run_id, level, ALERT_LEVEL.get(level, '一级提示'), metric, objects, action, note,
         db.now(), (user or {}).get('username', '')))
    db.audit(conn, 'monitor.ack', 'event', str(run_id or ''),
             f'{ALERT_LEVEL.get(level, "")} · {metric} · {objects} → {action} {note}', user)
    return Response(200, {'ok': True, 'events': _events(conn, run_id, 20)})


def list_events(conn: sqlite3.Connection, query: dict) -> Response:
    raw_run = (query.get('run_id') or [''])[0]
    run_id = int(raw_run) if str(raw_run).isdigit() else None
    limit = min(int((query.get('limit') or ['50'])[0] or 50), 200)
    return Response(200, {'items': _events(conn, run_id, limit)})


def handle(method: str, target: str, body: bytes, query: dict, headers: dict,
           settings=None) -> Optional[Response]:
    if not target.startswith('/api/monitor'):
        return None
    from app.config import get_settings
    settings = settings or get_settings()
    conn = db.connect(settings)
    try:
        user = _current_user(conn, headers)
        path = target.split('?', 1)[0].rstrip('/')
        segments = [seg for seg in path.split('/') if seg][2:]

        if method in ('GET', 'HEAD') and segments[:1] == ['state']:
            require(user, 'monitor.view')
            return current_state(conn, query)
        if method in ('GET', 'HEAD') and segments[:1] == ['events']:
            require(user, 'monitor.view')
            return list_events(conn, query)
        if method == 'POST' and segments[:1] == ['events']:
            require(user, 'monitor.handle')
            if not body:
                raise ApiProblem('请求体不能为空。')
            try:
                payload = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiProblem(f'请求体不是合法 JSON：{exc}') from exc
            return acknowledge(conn, payload, user)
        raise ApiProblem('不支持的监控接口。', status=404)
    finally:
        conn.close()


__all__ = ['ALERT_LEVEL', 'STATUS_ORDER', 'acknowledge', 'current_state', 'handle',
           'list_events', 'snapshot']
