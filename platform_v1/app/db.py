"""数据层：SQLite 库结构、连接管理、基础数据导入。

设计原则
--------
* 只用标准库 ``sqlite3``，库文件随项目走（默认 ``data/platform.db``），
  备份＝复制一个文件；
* 客户、车辆、参数是"可运营数据"，页面可增删改；``data/case2/*.csv``
  是首次初始化的种子，不覆盖用户已改过的数据；
* 每次生成方案写入 ``runs`` / ``trips`` / ``schedule_entries``，历史可回溯。

时间口径：库内时间窗、发车时刻统一存"自 00:00 起的分钟数"（INTEGER），
导入导出 CSV 时用 ``HH:MM``，两者通过 :func:`to_minutes` / :func:`to_hhmm` 互转。
"""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from app.config import APP_ROOT, Settings, get_settings

CASE2_DIR = APP_ROOT / 'data' / 'case2'

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    salt          TEXT    NOT NULL,
    role          TEXT    NOT NULL,
    display_name  TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    user_id     INTEGER,
    username    TEXT,
    role        TEXT,
    action      TEXT NOT NULL,
    object_type TEXT,
    object_id   TEXT,
    detail      TEXT,
    ip          TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS products (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    category         TEXT,
    unit_price       REAL,
    density_t_per_m3 REAL,
    temp_min         REAL,
    temp_max         REAL,
    mix_group        TEXT,
    loss_rate        REAL,
    active           INTEGER NOT NULL DEFAULT 1,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS params (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    unit       TEXT,
    category   TEXT,
    source     TEXT,
    note       TEXT,
    updated_at TEXT,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL,
    name          TEXT,
    product_scope TEXT    NOT NULL,
    lon           REAL    NOT NULL,
    lat           REAL    NOT NULL,
    customer_type TEXT,
    region        TEXT,
    service_min   INTEGER NOT NULL DEFAULT 0,
    exp_start     INTEGER,
    exp_end       INTEGER,
    acc_start     INTEGER,
    acc_end       INTEGER,
    batch_code    TEXT,
    note          TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT,
    UNIQUE (product_scope, code)
);

CREATE TABLE IF NOT EXISTS customer_demand (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    product_code TEXT    NOT NULL,
    demand_kg    REAL    NOT NULL DEFAULT 0,
    volume_m3    REAL
);
CREATE INDEX IF NOT EXISTS idx_demand_customer ON customer_demand(customer_id);

CREATE TABLE IF NOT EXISTS vehicles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT NOT NULL,
    name               TEXT,
    fleet_tag          TEXT NOT NULL DEFAULT 'baseline',
    vehicle_type       TEXT NOT NULL,
    energy_type        TEXT NOT NULL,
    capacity_kg        REAL,
    capacity_m3        REAL,
    box_size           TEXT,
    fixed_cost         REAL,
    unit_cost          REAL,
    maintenance_cost   REAL,
    range_km           REAL,
    effective_range_km REAL,
    energy_consumption TEXT,
    reefer_consumption TEXT,
    purchase_price_wan REAL,
    available          INTEGER NOT NULL DEFAULT 1,
    note               TEXT,
    created_at         TEXT,
    updated_at         TEXT,
    -- 车号只在所属车队内唯一：baseline 与 c3 允许出现同一个车号（如保留燃油车）
    UNIQUE (fleet_tag, code)
);

CREATE TABLE IF NOT EXISTS batches (
    code          TEXT NOT NULL,
    product_scope TEXT NOT NULL,
    depart_after  INTEGER,
    note          TEXT,
    PRIMARY KEY (code, product_scope)
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'done',
    solver     TEXT,
    scope      TEXT,
    fleet_tag  TEXT,
    params_json TEXT,
    kpi_json   TEXT,
    plan_json  TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS trips (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    trip_code             TEXT NOT NULL,
    batch_code            TEXT,
    product_scope         TEXT,
    customer_codes        TEXT,
    load_kg               REAL,
    volume_m3             REAL,
    distance_km           REAL,
    duration_min          REAL,
    planned_departure_min INTEGER,
    route_cost            REAL,
    energy_type           TEXT,
    note                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_trips_run ON trips(run_id);

CREATE TABLE IF NOT EXISTS schedule_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    trip_code    TEXT NOT NULL,
    vehicle_code TEXT,
    dock_code    TEXT,
    prep_start   INTEGER,
    prep_end     INTEGER,
    vehicle_arrival INTEGER,
    load_start   INTEGER,
    load_end     INTEGER,
    depart       INTEGER,
    wait_min     INTEGER,
    charge_start TEXT,
    charge_end   TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_run ON schedule_entries(run_id);

CREATE TABLE IF NOT EXISTS charging_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    vehicle_code TEXT NOT NULL,
    charger_code TEXT,
    start_min    INTEGER,
    end_min      INTEGER,
    energy_kwh   REAL,
    power_kw     REAL,
    cost_yuan    REAL,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    run_id      INTEGER,
    level       INTEGER,
    level_text  TEXT,
    object_type TEXT,
    object_code TEXT,
    object_codes TEXT,
    state       TEXT,
    metric      TEXT,
    value       REAL,
    threshold   REAL,
    message     TEXT,
    action      TEXT,
    note        TEXT,
    handled_by  TEXT,
    handled_at  TEXT,
    resolution  TEXT,
    created_at  TEXT,
    created_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def to_minutes(value) -> Optional[int]:
    """'07:30' / '7:30' / 450 / None -> 分钟数。"""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r'^(\d{1,2}):(\d{2})', text)
    if not match:
        try:
            return int(float(text))
        except ValueError:
            return None
    return int(match.group(1)) * 60 + int(match.group(2))


def to_hhmm(minutes) -> str:
    if minutes is None or minutes == '':
        return ''
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def db_path(settings: Optional[Settings] = None) -> Path:
    settings = settings or get_settings()
    path = Path(getattr(settings, 'db_file', None) or (APP_ROOT / 'data' / 'platform.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(settings: Optional[Settings] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path(settings)), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    return conn


def _read_csv(path: Path) -> list:
    raw = path.read_bytes()
    encoding = 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'utf-8'
    return list(csv.DictReader(raw.decode(encoding).splitlines()))


# --------------------------------------------------------------------------
# 初始化与种子
# --------------------------------------------------------------------------
def init_db(settings: Optional[Settings] = None, seed: bool = True) -> dict:
    """建表 + 首次导入基础数据。返回各表行数概览。"""
    conn = connect(settings)
    try:
        conn.executescript(SCHEMA)
        created = {}
        if seed:
            created['products'] = seed_products(conn)
            created['params'] = seed_params(conn)
            created['vehicles'] = seed_vehicles(conn)
            # 由现状车队派生"10 辆里换 6 辆纯电"的口径（fleet_tag = c3_10），
            # 与团队参数表的 8 辆口径并存，便于在页面上直接对比两种替换方案。
            created['vehicles_c3_10'] = seed_fleet_variant(conn)
            # 备选升级口径 C4：8 纯电 + 2 保留燃油（纯电比例高于 C3）
            created['vehicles_c4'] = seed_fleet_variant(conn, target_fleet='c4', ev_count=8)
            created['customers'] = seed_customers(conn)
            created['batches'] = seed_batches(conn)
        return {'seeded': created, 'counts': table_counts(conn)}
    finally:
        conn.close()


def table_counts(conn: sqlite3.Connection) -> dict:
    tables = ('users', 'customers', 'customer_demand', 'vehicles', 'products',
              'params', 'batches', 'runs', 'trips', 'events', 'audit_log')
    out = {}
    for table in tables:
        try:
            out[table] = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        except sqlite3.Error:
            out[table] = -1
    return out


def _is_empty(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def seed_products(conn: sqlite3.Connection) -> int:
    path = CASE2_DIR / 'products.csv'
    if not path.is_file() or not _is_empty(conn, 'products'):
        return 0
    rows = _read_csv(path)
    for row in rows:
        conn.execute(
            '''INSERT INTO products (code, name, category, unit_price, density_t_per_m3,
                                    temp_min, temp_max, mix_group, loss_rate, active, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?)''',
            (row['code'], row['name'], row['category'], float(row['unit_price'] or 0),
             float(row['density_t_per_m3'] or 0), float(row['temp_min'] or 0),
             float(row['temp_max'] or 0), row['mix_group'], float(row['loss_rate'] or 0), now()),
        )
    return len(rows)


def seed_params(conn: sqlite3.Connection) -> int:
    path = CASE2_DIR / 'params.csv'
    if not path.is_file() or not _is_empty(conn, 'params'):
        return 0
    rows = _read_csv(path)
    for row in rows:
        conn.execute(
            '''INSERT INTO params (key, value, unit, category, source, updated_at)
               VALUES (?,?,?,?,?,?)''',
            (row['key'], row['value'], row['unit'], row['category'], row['source'], now()),
        )
    return len(rows)


def seed_vehicles(conn: sqlite3.Connection) -> int:
    path = CASE2_DIR / 'vehicles.csv'
    if not path.is_file() or not _is_empty(conn, 'vehicles'):
        return 0
    rows = _read_csv(path)
    for row in rows:
        conn.execute(
            '''INSERT INTO vehicles (code, name, fleet_tag, vehicle_type, energy_type,
                   capacity_kg, capacity_m3, box_size, fixed_cost, unit_cost, maintenance_cost,
                   range_km, effective_range_km, energy_consumption, reefer_consumption,
                   purchase_price_wan, available, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (row['code'], row['name'], row.get('fleet_tag', 'baseline'), row['vehicle_type'],
             row['energy_type'], float(row['capacity_kg']), float(row['capacity_m3']),
             row['box_size'], float(row['fixed_cost']), float(row['unit_cost']),
             float(row['maintenance_cost']), float(row['range_km']),
             float(row['effective_range_km'] or row['range_km']),
             row['energy_consumption'], row['reefer_consumption'],
             float(row['purchase_price_wan']), int(row.get('available', 1) or 1), now(), now()),
        )
    return len(rows)


def seed_fleet_variant(conn: sqlite3.Connection, source_fleet: str = 'baseline',
                       target_fleet: str = 'c3_10', ev_count: int = 6) -> int:
    """由现状车队派生"10 辆车里换 6 辆纯电"的方案车队（fleet_tag = c3_10）。

    团队参数表写的是"纯电 6 + 保留燃油 2 = 8 辆"，而现状是 10 辆。两种口径对成本结论
    影响很大（车队规模直接决定固定成本与可用运力），因此把两种口径都做成可选车队，
    在「路径与方案」里切换即可对比，不必等人工确认。

    规则（确定性）：按编号排序取前 ev_count 辆作为被替换对象，沿用其车型与容积，
    能耗换成纯电口径（小型 0.42 kWh/km、中型 1.96 kWh/km），
    有效续航按同型号纯电车取值（小型 135 km、中型 189 km）。
    """
    existing = conn.execute('SELECT COUNT(*) FROM vehicles WHERE fleet_tag = ?',
                            (target_fleet,)).fetchone()[0]
    source_count = conn.execute('SELECT COUNT(*) FROM vehicles WHERE fleet_tag = ?',
                                (source_fleet,)).fetchone()[0]
    if existing or not source_count:
        return 0
    ev_spec = {
        '小型': {'kwh_per_km': 0.42, 'reefer_kwh_per_h': 1.4, 'effective_range_km': 135.0,
                 'fixed_cost': 200.0, 'unit_cost': 2.5, 'maintenance_cost': 40.0,
                 'range_km': 230.0, 'price_wan': 18.0},
        '中型': {'kwh_per_km': 1.96, 'reefer_kwh_per_h': 4.0, 'effective_range_km': 189.0,
                 'fixed_cost': 400.0, 'unit_cost': 4.0, 'maintenance_cost': 100.0,
                 'range_km': 260.0, 'price_wan': 26.0},
    }
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM vehicles WHERE fleet_tag = ? ORDER BY vehicle_type DESC, code', (source_fleet,))]
    inserted = 0
    for index, row in enumerate(rows):
        replaced = index < ev_count
        spec = ev_spec.get(row['vehicle_type'], ev_spec['小型'])
        if replaced:
            conn.execute(
                '''INSERT INTO vehicles (code, name, fleet_tag, vehicle_type, energy_type,
                       capacity_kg, capacity_m3, box_size, fixed_cost, unit_cost, maintenance_cost,
                       range_km, effective_range_km, energy_consumption, reefer_consumption,
                       purchase_price_wan, available, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f"E-{row['code']}", f"纯电{row['vehicle_type']}（替换 {row['code']}）", target_fleet,
                 row['vehicle_type'], '纯电', row['capacity_kg'], row['capacity_m3'], row['box_size'],
                 spec['fixed_cost'], spec['unit_cost'], spec['maintenance_cost'], spec['range_km'],
                 spec['effective_range_km'], f"{spec['kwh_per_km']} kWh/km",
                 f"{spec['reefer_kwh_per_h']} kWh/h", spec['price_wan'], 1, now(), now()))
        else:
            conn.execute(
                '''INSERT INTO vehicles (code, name, fleet_tag, vehicle_type, energy_type,
                       capacity_kg, capacity_m3, box_size, fixed_cost, unit_cost, maintenance_cost,
                       range_km, effective_range_km, energy_consumption, reefer_consumption,
                       purchase_price_wan, available, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f"K-{row['code']}", f"保留燃油{row['vehicle_type']}（原 {row['code']}）", target_fleet,
                 row['vehicle_type'], '燃油', row['capacity_kg'], row['capacity_m3'], row['box_size'],
                 row['fixed_cost'], row['unit_cost'], row['maintenance_cost'], row['range_km'],
                 row['effective_range_km'], row['energy_consumption'], row['reefer_consumption'],
                 row['purchase_price_wan'], 1, now(), now()))
        inserted += 1
    return inserted


def seed_batches(conn: sqlite3.Connection) -> int:
    path = CASE2_DIR / 'batches.csv'
    if not path.is_file() or not _is_empty(conn, 'batches'):
        return 0
    rows = _read_csv(path)
    for row in rows:
        conn.execute(
            'INSERT OR REPLACE INTO batches (code, product_scope, depart_after) VALUES (?,?,?)',
            (row['code'], row['product_scope'], to_minutes(row['depart_after'])),
        )
    return len(rows)


def seed_customers(conn: sqlite3.Connection) -> int:
    """导入姜蒜与鲜面条客户；需求按品类拆到 customer_demand。

    只有 customers 表为空时才导入，重复执行不会产生重复数据。
    """
    if not _is_empty(conn, 'customers'):
        return 0
    total = 0
    densities = {row['code']: float(row['density_t_per_m3'] or 0)
                 for row in conn.execute('SELECT code, density_t_per_m3 FROM products')}

    def demand_volume(kg: float, product_code: str) -> Optional[float]:
        density = densities.get(product_code) or 0
        if not density or not kg:
            return None
        return round(kg / 1000.0 / density, 3)

    gg_path = CASE2_DIR / 'customers_ginger_garlic.csv'
    if gg_path.is_file():
        for row in _read_csv(gg_path):
            cur = conn.execute(
                '''INSERT INTO customers (code, name, product_scope, lon, lat, customer_type, region,
                       service_min, exp_start, exp_end, acc_start, acc_end, batch_code, active,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)''',
                (row['customer_id'], row.get('name') or f"姜蒜客户{row['customer_id']}", '姜蒜',
                 float(row['lon']), float(row['lat']), row.get('customer_type', ''), row.get('region', ''),
                 int(float(row['service_min'] or 0)), to_minutes(row['exp_start']), to_minutes(row['exp_end']),
                 to_minutes(row['acc_start']), to_minutes(row['acc_end']), row.get('batch', ''), now(), now()),
            )
            cid = cur.lastrowid
            for product_code, key in (('garlic', 'garlic_kg'), ('ginger', 'ginger_kg')):
                kg = float(row.get(key) or 0)
                if kg:
                    conn.execute(
                        'INSERT INTO customer_demand (customer_id, product_code, demand_kg, volume_m3) VALUES (?,?,?,?)',
                        (cid, product_code, kg, demand_volume(kg, product_code)),
                    )
            total += 1

    nm_path = CASE2_DIR / 'customers_noodles.csv'
    if nm_path.is_file():
        for row in _read_csv(nm_path):
            cur = conn.execute(
                '''INSERT INTO customers (code, name, product_scope, lon, lat, customer_type, region,
                       service_min, exp_start, exp_end, acc_start, acc_end, batch_code, active,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)''',
                (row['customer_id'], row.get('name') or f"鲜面条客户{row['customer_id']}", '鲜面条',
                 float(row['lon']), float(row['lat']), row.get('customer_type', ''), row.get('region', ''),
                 int(float(row['service_min'] or 0)), to_minutes(row['exp_start']), to_minutes(row['exp_end']),
                 to_minutes(row['acc_start']), to_minutes(row['acc_end']), row.get('batch', ''), now(), now()),
            )
            cid = cur.lastrowid
            kg = float(row.get('demand_kg') or 0)
            if kg:
                conn.execute(
                    'INSERT INTO customer_demand (customer_id, product_code, demand_kg, volume_m3) VALUES (?,?,?,?)',
                    (cid, 'noodles', kg, demand_volume(kg, 'noodles')),
                )
            total += 1
    return total


# --------------------------------------------------------------------------
# 常用查询
# --------------------------------------------------------------------------
def list_customers(conn: sqlite3.Connection, product_scope: Optional[str] = None,
                   keyword: str = '', active_only: bool = False) -> list:
    sql = ['SELECT c.*, (SELECT GROUP_CONCAT(d.product_code || ":" || d.demand_kg, ",") '
           'FROM customer_demand d WHERE d.customer_id = c.id) AS demand_summary, '
           '(SELECT ROUND(IFNULL(SUM(d.demand_kg), 0), 2) FROM customer_demand d WHERE d.customer_id = c.id) AS total_kg, '
           '(SELECT ROUND(IFNULL(SUM(d.volume_m3), 0), 3) FROM customer_demand d WHERE d.customer_id = c.id) AS total_m3 '
           'FROM customers c WHERE 1=1']
    args: list = []
    if product_scope:
        sql.append('AND c.product_scope = ?')
        args.append(product_scope)
    if active_only:
        sql.append('AND c.active = 1')
    if keyword:
        sql.append('AND (c.code LIKE ? OR c.name LIKE ? OR c.region LIKE ? OR c.customer_type LIKE ?)')
        args.extend([f'%{keyword}%'] * 4)
    sql.append('ORDER BY c.product_scope, CAST(c.code AS INTEGER)')
    return [dict(row) for row in conn.execute(' '.join(sql), args)]


def list_vehicles(conn: sqlite3.Connection, fleet_tag: Optional[str] = None) -> list:
    if fleet_tag:
        rows = conn.execute('SELECT * FROM vehicles WHERE fleet_tag = ? ORDER BY vehicle_type, code', (fleet_tag,))
    else:
        rows = conn.execute('SELECT * FROM vehicles ORDER BY fleet_tag, vehicle_type, code')
    return [dict(row) for row in rows]


def get_params(conn: sqlite3.Connection) -> dict:
    return {row['key']: dict(row) for row in conn.execute('SELECT * FROM params')}


def set_param(conn: sqlite3.Connection, key: str, value, username: str = '') -> None:
    conn.execute('UPDATE params SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?',
                 (str(value), now(), username, key))


def list_batches(conn: sqlite3.Connection) -> list:
    return [dict(row) for row in conn.execute('SELECT * FROM batches ORDER BY product_scope, depart_after')]


def audit(conn: sqlite3.Connection, action: str, object_type: str = '', object_id: str = '',
          detail: str = '', user: Optional[dict] = None, ip: str = '') -> None:
    conn.execute(
        '''INSERT INTO audit_log (ts, user_id, username, role, action, object_type, object_id, detail, ip)
           VALUES (?,?,?,?,?,?,?,?,?)''',
        (now(), (user or {}).get('id'), (user or {}).get('username', 'system'),
         (user or {}).get('role', ''), action, object_type, str(object_id), detail, ip),
    )


__all__ = [
    'CASE2_DIR', 'SCHEMA', 'audit', 'connect', 'db_path', 'get_params', 'init_db',
    'list_batches', 'list_customers', 'list_vehicles', 'now', 'seed_customers',
    'set_param', 'table_counts', 'to_hhmm', 'to_minutes',
]
