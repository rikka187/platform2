"""管理类接口：登录会话、客户、车辆、参数、用户、留痕、距离校核。

与 ``app/api.py``（演示/求解接口）分开，便于单独演进。所有写操作都要
通过 :func:`app.auth.require` 的权限检查，并写 ``audit_log``。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Optional

from app import auth, db, labels
from app.config import Settings, get_settings
from app.http import (
    Response, clear_session_cookie, parse_cookies, session_cookie, session_token, split_path,
)
from simulation.geo_check import check_all, check_scope

TIME_FIELDS = ('exp_start', 'exp_end', 'acc_start', 'acc_end')
SCOPE_LABELS = {'姜蒜': '姜蒜', '鲜面条': '鲜面条'}
ALLOWED_SCOPES = ('姜蒜', '鲜面条')


class ApiProblem(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def _json_body(body: bytes) -> dict:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProblem(f'请求体不是合法 JSON：{exc}') from exc
    if not isinstance(payload, dict):
        raise ApiProblem('请求体必须是 JSON 对象。')
    return payload


def _int(value, field_name: str, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == '':
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ApiProblem(f'{field_name} 必须是整数。') from None


def _float(value, field_name: str, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == '':
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise ApiProblem(f'{field_name} 必须是数字。') from None


def _time_value(value, field_name: str) -> Optional[int]:
    """接受 'HH:MM'、'HH:MM:SS' 或分钟数。"""
    if value is None or value == '':
        return None
    minutes = db.to_minutes(value)
    if minutes is None:
        raise ApiProblem(f'{field_name} 需要 HH:MM 格式或分钟数。')
    if not 0 <= minutes <= 24 * 60:
        raise ApiProblem(f'{field_name} 超出 00:00-24:00 范围。')
    return minutes


def _context(settings: Optional[Settings] = None):
    settings = settings or get_settings()
    conn = db.connect(settings)
    return conn, settings


def _current_user(conn: sqlite3.Connection, headers: dict) -> Optional[dict]:
    return auth.get_session_user(conn, session_token(parse_cookies((headers or {}).get('Cookie'))))


# --------------------------------------------------------------------------
# 客户
# --------------------------------------------------------------------------
def _customer_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    for field in TIME_FIELDS:
        item[f'{field}_text'] = db.to_hhmm(item.get(field))
    return item


def _load_customer(conn: sqlite3.Connection, customer_id: int) -> dict:
    row = conn.execute('SELECT * FROM customers WHERE id = ?', (customer_id,)).fetchone()
    if not row:
        raise ApiProblem(f'客户不存在：{customer_id}', status=404)
    item = _customer_row(row)
    item['demands'] = [dict(r) for r in conn.execute(
        'SELECT product_code, demand_kg, volume_m3 FROM customer_demand WHERE customer_id = ? '
        'ORDER BY product_code', (customer_id,))]
    return item


def _validate_customer(payload: dict, partial: bool = False) -> dict:
    out: dict = {}
    if not partial or 'code' in payload:
        code = str(payload.get('code') or '').strip()
        if not code:
            raise ApiProblem('客户编号不能为空。')
        out['code'] = code
    if not partial or 'product_scope' in payload:
        scope = str(payload.get('product_scope') or '').strip()
        if scope not in ALLOWED_SCOPES:
            raise ApiProblem('产品品类只能是 姜蒜 或 鲜面条。')
        out['product_scope'] = scope
    if not partial or 'lon' in payload:
        lon = _float(payload.get('lon'), '经度')
        if lon is None or not 73 <= lon <= 136:
            raise ApiProblem('经度应在 73~136 之间（中国境内）。')
        out['lon'] = lon
    if not partial or 'lat' in payload:
        lat = _float(payload.get('lat'), '纬度')
        if lat is None or not 3 <= lat <= 54:
            raise ApiProblem('纬度应在 3~54 之间（中国境内）。')
        out['lat'] = lat
    for field in TIME_FIELDS:
        if field in payload:
            out[field] = _time_value(payload.get(field), field)
    if 'service_min' in payload:
        service = _int(payload.get('service_min'), '服务时间', 0)
        if service is None or service < 0 or service > 480:
            raise ApiProblem('服务时间应在 0~480 分钟之间。')
        out['service_min'] = service
    for field in ('name', 'customer_type', 'region', 'batch_code', 'note'):
        if field in payload:
            out[field] = str(payload.get(field) or '').strip()
    # 时间窗大小关系校验
    exp_start = out.get('exp_start', payload.get('exp_start'))
    exp_end = out.get('exp_end', payload.get('exp_end'))
    acc_start = out.get('acc_start', payload.get('acc_start'))
    acc_end = out.get('acc_end', payload.get('acc_end'))
    if exp_start is not None and exp_end is not None and exp_start > exp_end:
        raise ApiProblem('期望时间窗的开始时间不能晚于结束时间。')
    if acc_start is not None and acc_end is not None and acc_start > acc_end:
        raise ApiProblem('可接受时间窗的开始时间不能晚于结束时间。')
    if exp_start is not None and acc_start is not None and exp_start < acc_start:
        raise ApiProblem('期望时间窗必须落在可接受时间窗之内（开始）。')
    if exp_end is not None and acc_end is not None and exp_end > acc_end:
        raise ApiProblem('期望时间窗必须落在可接受时间窗之内（结束）。')
    return out


def _save_demands(conn: sqlite3.Connection, customer_id: int, demands) -> None:
    if demands is None:
        return
    if not isinstance(demands, list):
        raise ApiProblem('demands 必须是数组，如 [{"product_code":"garlic","demand_kg":80}]。')
    conn.execute('DELETE FROM customer_demand WHERE customer_id = ?', (customer_id,))
    products = {row['code']: dict(row) for row in conn.execute('SELECT * FROM products')}
    for item in demands:
        if not isinstance(item, dict):
            raise ApiProblem('demands 的元素必须是对象。')
        code = str(item.get('product_code') or '').strip()
        if code not in products:
            raise ApiProblem(f'未知品类：{code}（可选：{", ".join(sorted(products))}）')
        kg = _float(item.get('demand_kg'), f'{code} 需求量', 0.0) or 0.0
        if kg < 0:
            raise ApiProblem('需求量不能为负数。')
        volume = item.get('volume_m3')
        if volume in (None, ''):
            density = products[code].get('density_t_per_m3') or 0
            volume = round(kg / 1000.0 / density, 3) if density else None
        else:
            volume = _float(volume, f'{code} 体积')
        conn.execute(
            'INSERT INTO customer_demand (customer_id, product_code, demand_kg, volume_m3) VALUES (?,?,?,?)',
            (customer_id, code, kg, volume))


def list_customers(conn: sqlite3.Connection, query: dict) -> Response:
    scope = (query.get('scope') or [''])[0]
    keyword = (query.get('keyword') or [''])[0]
    rows = db.list_customers(conn, scope or None, keyword)
    for row in rows:
        for field in TIME_FIELDS:
            row[f'{field}_text'] = db.to_hhmm(row.get(field))
    summary = {
        'total': len(rows),
        'by_scope': {},
        'total_kg': round(sum(r.get('total_kg') or 0 for r in rows), 2),
        'total_m3': round(sum(r.get('total_m3') or 0 for r in rows), 3),
    }
    for row in rows:
        key = row['product_scope']
        summary['by_scope'][key] = summary['by_scope'].get(key, 0) + 1
    products = [dict(r) for r in conn.execute(
        'SELECT code, name, category, unit_price, density_t_per_m3 FROM products ORDER BY code')]
    batches = [dict(r) for r in conn.execute('SELECT * FROM batches ORDER BY product_scope, depart_after')]
    return Response(200, {'items': rows, 'summary': summary, 'products': products, 'batches': batches})


def create_customer(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    data = _validate_customer(payload)
    data.setdefault('service_min', 0)          # 表定义为 NOT NULL DEFAULT 0，缺省时显式补 0
    exists = conn.execute('SELECT 1 FROM customers WHERE product_scope = ? AND code = ?',
                          (data['product_scope'], data['code'])).fetchone()
    if exists:
        raise ApiProblem(f"{data['product_scope']} 品类下已存在编号 {data['code']} 的客户。")
    # 只写显式给出的列，其余交给表默认值，避免用 None 撞 NOT NULL
    allowed = ['code', 'name', 'product_scope', 'lon', 'lat', 'customer_type', 'region',
               'service_min', 'exp_start', 'exp_end', 'acc_start', 'acc_end', 'batch_code', 'note']
    columns = [col for col in allowed if col in data]
    values = [data[col] for col in columns]
    cur = conn.execute(
        f"INSERT INTO customers ({','.join(columns)}, active, created_at, updated_at) "
        f"VALUES ({','.join('?' * len(columns))}, 1, ?, ?)",
        values + [db.now(), db.now()])
    customer_id = cur.lastrowid
    _save_demands(conn, customer_id, payload.get('demands'))
    item = _load_customer(conn, customer_id)
    db.audit(conn, 'customer.create', 'customer', str(customer_id),
             f"{item['product_scope']} 客户 {item['code']} 新增", user)
    return Response(200, {'item': item, 'geo_check': check_scope(conn, item['product_scope'])})


def update_customer(conn: sqlite3.Connection, customer_id: int, payload: dict, user: dict) -> Response:
    before = _load_customer(conn, customer_id)
    data = _validate_customer(payload, partial=True)
    sets = [f'{key} = ?' for key in data]
    args = list(data.values())
    sets.append('updated_at = ?')
    args.append(db.now())
    args.append(customer_id)
    conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id = ?", args)
    _save_demands(conn, customer_id, payload.get('demands'))
    item = _load_customer(conn, customer_id)
    changed = [key for key in data if str(before.get(key)) != str(item.get(key))]
    db.audit(conn, 'customer.update', 'customer', str(customer_id),
             f"{item['product_scope']} 客户 {item['code']} 修改：{','.join(changed) or 'demands'}", user)
    return Response(200, {'item': item, 'geo_check': check_scope(conn, item['product_scope'])})


def delete_customer(conn: sqlite3.Connection, customer_id: int, user: dict) -> Response:
    item = _load_customer(conn, customer_id)
    conn.execute('DELETE FROM customers WHERE id = ?', (customer_id,))
    db.audit(conn, 'customer.delete', 'customer', str(customer_id),
             f"{item['product_scope']} 客户 {item['code']} 删除", user)
    return Response(200, {'deleted': customer_id, 'geo_check': check_scope(conn, item['product_scope'])})


def export_customers(conn: sqlite3.Connection, query: dict) -> Response:
    scope = (query.get('scope') or [''])[0]
    rows = db.list_customers(conn, scope or None)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['product_scope', 'customer_id', 'name', 'lon', 'lat', 'customer_type', 'region',
                     'service_min', 'exp_start', 'exp_end', 'acc_start', 'acc_end', 'batch_code',
                     'demands', 'total_kg', 'total_m3'])
    for row in rows:
        writer.writerow([
            row['product_scope'], row['code'], row.get('name', ''), row['lon'], row['lat'],
            row.get('customer_type', ''), row.get('region', ''), row['service_min'],
            db.to_hhmm(row['exp_start']), db.to_hhmm(row['exp_end']),
            db.to_hhmm(row['acc_start']), db.to_hhmm(row['acc_end']), row.get('batch_code', ''),
            row.get('demand_summary', ''), row.get('total_kg', 0), row.get('total_m3', 0),
        ])
    return Response(200, {'filename': f'customers_{scope or "all"}.csv',
                          'content': '\ufeff' + buffer.getvalue(), 'count': len(rows)})


def import_customers(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    content = payload.get('content')
    if not content:
        raise ApiProblem('缺少 content（CSV 文本）。')
    scope = str(payload.get('product_scope') or '').strip()
    rows = list(csv.DictReader(io.StringIO(str(content).lstrip('\ufeff'))))
    if not rows:
        raise ApiProblem('CSV 没有数据行。')
    header = {key.strip().lower() for key in rows[0].keys() if key}
    if not {'customer_id', 'lon', 'lat'} <= header:
        raise ApiProblem('CSV 至少需要 customer_id、lon、lat 三列。')

    created = updated = 0
    for row in rows:
        clean = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
        code = clean.get('customer_id') or clean.get('code')
        if not code:
            continue
        row_scope = clean.get('product_scope') or scope
        if row_scope not in ALLOWED_SCOPES:
            row_scope = '姜蒜' if clean.get('garlic_kg') or clean.get('ginger_kg') else '鲜面条'
        payload_row = {
            'code': code, 'product_scope': row_scope, 'lon': clean.get('lon'), 'lat': clean.get('lat'),
            'name': clean.get('name') or '', 'customer_type': clean.get('customer_type', ''),
            'region': clean.get('region', ''), 'service_min': clean.get('service_min') or 0,
            'exp_start': clean.get('exp_start', ''), 'exp_end': clean.get('exp_end', ''),
            'acc_start': clean.get('acc_start', ''), 'acc_end': clean.get('acc_end', ''),
            'batch_code': clean.get('batch', '') or clean.get('batch_code', ''),
            'demands': [],
        }
        if row_scope == '姜蒜':
            if clean.get('garlic_kg'):
                payload_row['demands'].append({'product_code': 'garlic', 'demand_kg': clean['garlic_kg']})
            if clean.get('ginger_kg'):
                payload_row['demands'].append({'product_code': 'ginger', 'demand_kg': clean['ginger_kg']})
        elif clean.get('demand_kg'):
            payload_row['demands'].append({'product_code': 'noodles', 'demand_kg': clean['demand_kg']})

        existing = conn.execute('SELECT id FROM customers WHERE product_scope = ? AND code = ?',
                                (row_scope, code)).fetchone()
        if existing:
            update_customer(conn, existing['id'], payload_row, user)
            updated += 1
        else:
            create_customer(conn, payload_row, user)
            created += 1
    db.audit(conn, 'customer.import', 'customer', scope or 'all',
             f'CSV 导入：新增 {created}，更新 {updated}', user)
    return Response(200, {'created': created, 'updated': updated,
                          'geo_check': check_all(conn)})


# --------------------------------------------------------------------------
# 车辆
# --------------------------------------------------------------------------
VEHICLE_FIELDS = ('code', 'name', 'fleet_tag', 'vehicle_type', 'energy_type', 'capacity_kg',
                  'capacity_m3', 'box_size', 'fixed_cost', 'unit_cost', 'maintenance_cost',
                  'range_km', 'effective_range_km', 'energy_consumption', 'reefer_consumption',
                  'purchase_price_wan', 'available', 'note')
NUMERIC_FIELDS = ('capacity_kg', 'capacity_m3', 'fixed_cost', 'unit_cost', 'maintenance_cost',
                  'range_km', 'effective_range_km', 'purchase_price_wan')


def _validate_vehicle(payload: dict, partial: bool = False) -> dict:
    out: dict = {}
    if not partial or 'code' in payload:
        code = str(payload.get('code') or '').strip()
        if not code:
            raise ApiProblem('车辆编号不能为空。')
        out['code'] = code
    if not partial or 'vehicle_type' in payload:
        vtype = str(payload.get('vehicle_type') or '').strip()
        if vtype not in ('小型', '中型'):
            raise ApiProblem('车型只能是 小型 或 中型。')
        out['vehicle_type'] = vtype
    if not partial or 'energy_type' in payload:
        energy = str(payload.get('energy_type') or '').strip()
        if energy not in ('燃油', '纯电'):
            raise ApiProblem('能源类型只能是 燃油 或 纯电。')
        out['energy_type'] = energy
    for field in NUMERIC_FIELDS:
        if field in payload:
            value = _float(payload.get(field), field)
            if value is not None and value < 0:
                raise ApiProblem(f'{field} 不能为负数。')
            out[field] = value
    if 'available' in payload:
        out['available'] = 1 if str(payload.get('available')).lower() not in ('0', 'false', 'no', '') else 0
    for field in ('name', 'fleet_tag', 'box_size', 'energy_consumption', 'reefer_consumption', 'note'):
        if field in payload:
            out[field] = str(payload.get(field) or '').strip()
    out.setdefault('fleet_tag', None)
    return {k: v for k, v in out.items() if v is not None or k in ('name', 'box_size', 'note',
                                                                  'energy_consumption', 'reefer_consumption')}


def list_vehicles(conn: sqlite3.Connection, query: dict) -> Response:
    fleet = (query.get('fleet') or [''])[0]
    items = db.list_vehicles(conn, fleet or None)
    for item in items:                      # 中文标签：车队口径
        item['fleet_label'] = labels.fleet_label(item.get('fleet_tag'))
    summary = {}
    for item in items:
        key = (item.get('fleet_tag') or 'baseline', item['vehicle_type'], item['energy_type'])
        summary.setdefault(str(key), 0)
        summary[str(key)] += 1
    fleets = []
    seen = set()
    for item in items:
        tag = item.get('fleet_tag') or 'baseline'
        if tag not in seen:
            seen.add(tag)
            fleets.append({'tag': tag, 'label': labels.fleet_label(tag)})
    return Response(200, {'items': items, 'fleets': fleets, 'summary': {
        'total': len(items),
        'fuel': sum(1 for i in items if i['energy_type'] == '燃油'),
        'electric': sum(1 for i in items if i['energy_type'] == '纯电'),
        'small': sum(1 for i in items if i['vehicle_type'] == '小型'),
        'medium': sum(1 for i in items if i['vehicle_type'] == '中型'),
    }})


def create_vehicle(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    data = _validate_vehicle(payload)
    data.setdefault('fleet_tag', 'baseline')
    exists = conn.execute('SELECT 1 FROM vehicles WHERE fleet_tag = ? AND code = ?',
                          (data['fleet_tag'], data['code'])).fetchone()
    if exists:
        raise ApiProblem(f"车队 {data['fleet_tag']} 下已存在车号 {data['code']}。")
    columns = [f for f in VEHICLE_FIELDS if f in data]
    values = [data[f] for f in columns]
    cur = conn.execute(
        f"INSERT INTO vehicles ({','.join(columns)}, available, created_at, updated_at) "
        f"VALUES ({','.join('?' * len(columns))}, ?, ?, ?)",
        values + [int(data.get('available', 1)), db.now(), db.now()])
    db.audit(conn, 'vehicle.create', 'vehicle', str(cur.lastrowid),
             f"新增车辆 {data['fleet_tag']}/{data['code']}（{data['vehicle_type']}·{data['energy_type']}）", user)
    row = conn.execute('SELECT * FROM vehicles WHERE id = ?', (cur.lastrowid,)).fetchone()
    return Response(200, {'item': dict(row)})


def update_vehicle(conn: sqlite3.Connection, vehicle_id: int, payload: dict, user: dict) -> Response:
    row = conn.execute('SELECT * FROM vehicles WHERE id = ?', (vehicle_id,)).fetchone()
    if not row:
        raise ApiProblem(f'车辆不存在：{vehicle_id}', status=404)
    data = _validate_vehicle(payload, partial=True)
    if data:
        sets = [f'{key} = ?' for key in data]
        args = list(data.values()) + [db.now(), vehicle_id]
        conn.execute(f"UPDATE vehicles SET {', '.join(sets)}, updated_at = ? WHERE id = ?", args)
    db.audit(conn, 'vehicle.update', 'vehicle', str(vehicle_id),
             f"修改车辆 {row['code']}：{','.join(data.keys()) or '无字段'}", user)
    item = conn.execute('SELECT * FROM vehicles WHERE id = ?', (vehicle_id,)).fetchone()
    return Response(200, {'item': dict(item)})


def delete_vehicle(conn: sqlite3.Connection, vehicle_id: int, user: dict) -> Response:
    row = conn.execute('SELECT * FROM vehicles WHERE id = ?', (vehicle_id,)).fetchone()
    if not row:
        raise ApiProblem(f'车辆不存在：{vehicle_id}', status=404)
    conn.execute('DELETE FROM vehicles WHERE id = ?', (vehicle_id,))
    db.audit(conn, 'vehicle.delete', 'vehicle', str(vehicle_id),
             f"删除车辆 {row['fleet_tag']}/{row['code']}", user)
    return Response(200, {'deleted': vehicle_id})


# --------------------------------------------------------------------------
# 参数 / 用户 / 留痕 / 距离校核
# --------------------------------------------------------------------------
def list_params(conn: sqlite3.Connection) -> Response:
    rows = [dict(r) for r in conn.execute('SELECT * FROM params ORDER BY category, key')]
    grouped = {}
    for row in rows:
        # 中文名称 / 单位 / 口径说明 / 分组（数据库仍存英文键名，展示一律走这里）
        row.update(labels.param_label(row['key']))
        row['group'] = labels.param_group(row['key'])
        grouped.setdefault(row['group'], []).append(row)
    groups = [{'title': title, 'items': items} for title, items in grouped.items()]
    return Response(200, {'items': rows, 'grouped': grouped, 'groups': groups, 'count': len(rows)})


def update_params(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    updates = payload.get('updates')
    if not isinstance(updates, dict) or not updates:
        raise ApiProblem('updates 需要形如 {"penalty_late": 20}。')
    known = {row['key'] for row in conn.execute('SELECT key FROM params')}
    changed = []
    for key, value in updates.items():
        if key not in known:
            raise ApiProblem(f'未知参数：{key}')
        db.set_param(conn, key, value, user.get('username', ''))
        changed.append(f'{key}={value}')
    db.audit(conn, 'params.update', 'params', 'batch', '；'.join(changed), user)
    return list_params(conn)


def list_users(conn: sqlite3.Connection) -> Response:
    return Response(200, {'items': auth.list_users(conn), 'roles': auth.ROLE_LABELS})


def create_user(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    created = auth.create_user(conn, payload.get('username', ''), payload.get('password', ''),
                               payload.get('role', ''), payload.get('display_name', ''), operator=user)
    return Response(200, {'item': created})


def update_user(conn: sqlite3.Connection, user_id: int, payload: dict, user: dict) -> Response:
    target = auth.get_user(conn, user_id)
    if not target:
        raise ApiProblem(f'用户不存在：{user_id}', status=404)
    if 'role' in payload:
        if target['username'] == user.get('username') and payload['role'] != target['role']:
            raise ApiProblem('不能修改自己的角色。')
        auth.set_role(conn, user_id, payload['role'], operator=user)
    if 'active' in payload:
        if target['username'] == user.get('username'):
            raise ApiProblem('不能停用自己的账号。')
        auth.set_active(conn, user_id, str(payload['active']).lower() not in ('0', 'false', 'no', ''), operator=user)
    if payload.get('password'):
        auth.set_password(conn, user_id, payload['password'], operator=user)
    return Response(200, {'item': auth.get_user(conn, user_id)})


def list_audit(conn: sqlite3.Connection, query: dict) -> Response:
    limit = min(int((query.get('limit') or ['100'])[0] or 100), 500)
    rows = conn.execute(
        'SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (limit,))
    items = []
    for row in rows:
        item = dict(row)
        item['action_label'] = labels.action_label(item.get('action'))     # 中文动作名
        item['role_label'] = labels.role_label(item.get('role'))
        items.append(item)
    return Response(200, {'items': items, 'limit': limit})


def geo_check(conn: sqlite3.Connection, query: dict) -> Response:
    scope = (query.get('scope') or [''])[0]
    if scope:
        if scope not in ALLOWED_SCOPES:
            raise ApiProblem('未知品类。')
        return Response(200, {'results': [check_scope(conn, scope)]})
    return Response(200, check_all(conn))


# --------------------------------------------------------------------------
# 登录会话
# --------------------------------------------------------------------------
def _secure_cookie(settings: Settings) -> bool:
    return bool(getattr(settings, 'cookie_secure', False))


def login(conn: sqlite3.Connection, payload: dict, settings: Settings, headers: dict) -> Response:
    username = payload.get('username', '')
    password = payload.get('password', '')
    ip = str((headers or {}).get('X-Real-IP') or (headers or {}).get('X-Forwarded-For') or '')
    user = auth.authenticate(conn, username, password, ip=ip)
    auth.purge_expired_sessions(conn)
    token = auth.create_session(conn, user['id'], ip=ip,
                                user_agent=str((headers or {}).get('User-Agent', '')))
    response = Response(200, {
        'user': user,
        'must_change_password': bool(user.get('must_change_password')),
        'roles': auth.ROLE_LABELS,
        'permissions': sorted(auth.PERMISSIONS.get(user['role'], set())),
    })
    response.with_header('Set-Cookie', session_cookie(token, secure=_secure_cookie(settings)))
    return response


def logout(conn: sqlite3.Connection, headers: dict, settings: Settings) -> Response:
    token = session_token(parse_cookies((headers or {}).get('Cookie')))
    if token:
        user = auth.get_session_user(conn, token)
        auth.delete_session(conn, token)
        if user:
            db.audit(conn, 'logout', 'user', user['username'], '退出登录', user)
    response = Response(200, {'ok': True})
    response.with_header('Set-Cookie', clear_session_cookie(secure=_secure_cookie(settings)))
    return response


def current_user(conn: sqlite3.Connection, headers: dict) -> Response:
    user = _current_user(conn, headers)
    if not user:
        return Response(401, {'error': '未登录。', 'user': None})
    return Response(200, {
        'user': user,
        'must_change_password': bool(user.get('must_change_password')),
        'roles': auth.ROLE_LABELS,
        'permissions': sorted(auth.PERMISSIONS.get(user['role'], set())),
        'labels': auth.ROLE_LABELS,
    })


def change_password(conn: sqlite3.Connection, payload: dict, headers: dict) -> Response:
    user = _current_user(conn, headers)
    if not user:
        raise ApiProblem('未登录。', status=401)
    old = payload.get('old_password', '')
    new = payload.get('new_password', '')
    row = auth.get_user_by_name(conn, user['username'])
    if not auth.verify_password(old, row['password_hash'], row['salt']):
        raise ApiProblem('原口令不正确。')
    if old == new:
        raise ApiProblem('新口令不能与原口令相同。')
    auth.set_password(conn, user['id'], new, must_change=False, operator=user)
    return Response(200, {'ok': True})


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------
def handle(method: str, target: str, body: bytes, query: dict, headers: dict,
           settings: Optional[Settings] = None) -> Optional[Response]:
    """管理类路由分发；不属于本模块的路径返回 None 交给上层。"""
    segments = split_path(target)
    if not segments or segments[0] != 'api':
        return None
    parts = segments[1:]
    if not parts:
        return None
    head = parts[0]

    # ---- 登录相关（无需权限） ----
    if head == 'auth':
        conn, settings = _context(settings)
        try:
            action = parts[1] if len(parts) > 1 else ''
            if action == 'login' and method == 'POST':
                return login(conn, _json_body(body), settings, headers)
            if action == 'logout' and method == 'POST':
                return logout(conn, headers, settings)
            if action == 'me' and method in ('GET', 'HEAD'):
                return current_user(conn, headers)
            if action == 'password' and method == 'POST':
                return change_password(conn, _json_body(body), headers)
            raise ApiProblem('未知的登录接口。', status=404)
        finally:
            conn.close()

    # ---- 其余接口需要会话 + 权限 ----
    conn, settings = _context(settings)
    try:
        user = _current_user(conn, headers)

        if head == 'customers':
            if method in ('GET', 'HEAD') and len(parts) == 2 and parts[1] == 'export':
                auth.require(user, 'customers.view')
                return export_customers(conn, query)
            if method in ('GET', 'HEAD') and len(parts) == 1:
                auth.require(user, 'customers.view')
                return list_customers(conn, query)
            if method == 'POST' and len(parts) == 2 and parts[1] == 'import':
                auth.require(user, 'customers.edit')
                return import_customers(conn, _json_body(body), user)
            if method == 'POST' and len(parts) == 1:
                auth.require(user, 'customers.edit')
                return create_customer(conn, _json_body(body), user)
            if len(parts) == 2:
                customer_id = _int(parts[1], '客户 ID')
                auth.require(user, 'customers.edit')
                if method in ('PATCH', 'PUT', 'POST'):
                    return update_customer(conn, customer_id, _json_body(body), user)
                if method == 'DELETE':
                    return delete_customer(conn, customer_id, user)
                if method in ('GET', 'HEAD'):
                    auth.require(user, 'customers.view')
                    return Response(200, {'item': _load_customer(conn, customer_id)})
            raise ApiProblem('不支持的客户接口。', status=404)

        if head == 'vehicles':
            if method in ('GET', 'HEAD') and len(parts) == 1:
                auth.require(user, 'vehicles.view')
                return list_vehicles(conn, query)
            if method == 'POST' and len(parts) == 1:
                auth.require(user, 'vehicles.edit')
                return create_vehicle(conn, _json_body(body), user)
            if len(parts) == 2:
                vehicle_id = _int(parts[1], '车辆 ID')
                auth.require(user, 'vehicles.edit')
                if method in ('PATCH', 'PUT', 'POST'):
                    return update_vehicle(conn, vehicle_id, _json_body(body), user)
                if method == 'DELETE':
                    return delete_vehicle(conn, vehicle_id, user)
            raise ApiProblem('不支持的车辆接口。', status=404)

        if head == 'params':
            if method in ('GET', 'HEAD'):
                auth.require(user, 'vehicles.view')
                return list_params(conn)
            if method in ('PATCH', 'PUT', 'POST'):
                auth.require(user, 'vehicles.edit')
                return update_params(conn, _json_body(body), user)
            raise ApiProblem('不支持的参数接口。', status=404)

        if head == 'users':
            auth.require(user, 'users.manage')
            if method in ('GET', 'HEAD') and len(parts) == 1:
                return list_users(conn)
            if method == 'POST' and len(parts) == 1:
                return create_user(conn, _json_body(body), user)
            if len(parts) == 2 and method in ('PATCH', 'PUT', 'POST'):
                return update_user(conn, _int(parts[1], '用户 ID'), _json_body(body), user)
            raise ApiProblem('不支持的用户接口。', status=404)

        if head == 'audit':
            auth.require(user, 'audit.view')
            return list_audit(conn, query)

        if head == 'geo':
            if len(parts) > 1 and parts[1] == 'check':
                auth.require(user, 'customers.view')
                return geo_check(conn, query)
            raise ApiProblem('未知的地理接口。', status=404)

        return None
    finally:
        conn.close()


__all__ = ['ApiProblem', 'handle', 'check_all', 'check_scope']
