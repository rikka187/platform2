"""方案生成接口：客户→批次→趟次 → 现有求解器 → 选题口径 KPI，并把方案落库。

情景（论文 4.5.4.2）
--------------------
===== ========================== ============ ==========================
情景   策略组合                    车队          映射到的求解器情景
===== ========================== ============ ==========================
S0    人工调度基准                 现状 10 辆     H0（里程按 1.22 折算、等待口径 120→30 截断）
S1    仅路径优化                   现状 10 辆     S1（里程不折算）
S2    路径优化 + 车辆新能源         C3            C3（优先纯电、长线保留燃油）
S3    路径优化 + 新能源 + 月台协同  C3            D3（最短等待口径 18 分钟）
===== ========================== ============ ==========================

求解器本身不做任何修改（``simulation.scheduler.solve_scenario``）；
本模块只负责组织输入、调用求解器、按选题表2-5/2-6 定价、落库与回放。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app import db, labels
from app.api_admin import ApiProblem, _current_user
from app.auth import require
from app.config import Settings, get_settings
from app.http import Response
from simulation import charging, costing, docks, joint_plan, lifecycle, planning, scoring
from simulation import vrp_optimizer
from simulation.scenarios import SCENARIO_DISTANCE_FACTOR
from simulation.scheduler import solve_scenario

# 平台情景 → (求解器情景, 默认车队)
SCENARIO_MAP = {
    'S0': ('H0', 'baseline'),
    'S1': ('S1', 'baseline'),
    'S2': ('C3', 'c3'),
    'S3': ('D3', 'c3'),
}
SCENARIO_LABELS = {
    'S0': '人工调度基准',
    'S1': '仅路径优化',
    'S2': '路径优化 + 车辆新能源',
    'S3': '路径优化 + 新能源 + 仓配月台协同',
}
WAIT_BY_SOLVER_SCENARIO = {'H0': 120, 'S0': 72, 'S1': 60, 'C3': 60, 'D3': 18}
# 车队口径：baseline = 现状 10 辆；c3 = 团队参数表口径（6 纯电 + 2 保留燃油 = 8 辆）；
# c3_10 = 「10 辆里换 6 辆纯电」，用于回答"C3 到底几辆车"这个口径问题。
FLEET_LABELS = {
    'baseline': '现状 10 辆（8 小 + 2 中，全燃油）',
    'c3': 'C3 参数表口径 8 辆（6 纯电 + 2 保留燃油）',
    'c3_10': 'C3 替换口径 10 辆（6 纯电 + 4 保留燃油）',
}


def _body(body: bytes) -> dict:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProblem(f'请求体不是合法 JSON：{exc}') from exc
    if not isinstance(payload, dict):
        raise ApiProblem('请求体必须是 JSON 对象。')
    return payload


def trips_with_prep(plan: dict) -> list:
    """趟次补充 ``prep_min``（拣货/组盘时长），供月台月台协同做备货反推。"""
    out = []
    for trip in plan.get('trips', []):
        item = dict(trip)
        item['prep_min'] = max(10, int(round(float(trip.get('service_total_min') or 10))))
        out.append(item)
    return out


def _store_run(conn: sqlite3.Connection, scenario: str, scope: str, fleet_tag: str, solver: str,
               params: dict, plan: dict, solution: dict, kpi: dict, user: Optional[dict]) -> int:
    cur = conn.execute(
        '''INSERT INTO runs (scenario, status, solver, scope, fleet_tag, params_json, kpi_json,
                             plan_json, created_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (scenario, 'done', solver, scope, fleet_tag, json.dumps(params, ensure_ascii=False),
         json.dumps(kpi, ensure_ascii=False),
         json.dumps({'plan': plan, 'solution': solution}, ensure_ascii=False),
         db.now(), (user or {}).get('username', '')))
    run_id = cur.lastrowid

    for trip in plan['trips']:
        conn.execute(
            '''INSERT INTO trips (run_id, trip_code, batch_code, product_scope, customer_codes,
                   load_kg, volume_m3, distance_km, duration_min, planned_departure_min,
                   route_cost, energy_type, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (run_id, trip['trip_id'], trip['batch_id'], trip['product'],
             ','.join(trip['customer_codes']), trip['load_kg'], trip['volume_m3'],
             trip['distance_km'], trip['duration_min'], trip['planned_departure_min'],
             round(float(trip.get('fixed_cost') or 0) + float(trip.get('unit_cost') or 0) * trip['distance_km'], 2),
             trip.get('energy_type'), 'infeasible' if trip.get('infeasible') else ''))

    for entry in solution.get('schedule', []):
        conn.execute(
            '''INSERT INTO schedule_entries (run_id, trip_code, vehicle_code, dock_code, prep_start,
                   prep_end, vehicle_arrival, load_start, load_end, depart, wait_min,
                   charge_start, charge_end)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (run_id, entry.get('trip_id'), entry.get('vehicle_id'), entry.get('dock_id'),
             entry.get('prep_start'), entry.get('prep_end'), entry.get('vehicle_arrival'),
             entry.get('load_start'), entry.get('load_end'), entry.get('depart'),
             entry.get('wait_min'), entry.get('charge_start'), entry.get('charge_end')))

    for charging in (plan['solver_data'].get('charging') or []):
        conn.execute(
            '''INSERT INTO charging_plans (run_id, vehicle_code, charger_code, start_min, end_min, note)
               VALUES (?,?,?,?,?,?)''',
            (run_id, charging['vehicle_id'], charging.get('charger_id'),
             db.to_minutes(charging['charge_start']), db.to_minutes(charging['charge_end']),
             '谷段充电 01:00-07:00，单次 45 分钟'))

    db.audit(conn, 'plan.run', 'run', str(run_id),
             f"{scenario}（{SCENARIO_LABELS.get(scenario, scenario)}）{scope}/{fleet_tag}："
             f"{kpi.get('trips')} 趟次，总成本 {kpi.get('total_cost')} 元", user)
    return run_id


def _run_scenario(conn: sqlite3.Connection, scenario: str, scope: str, fleet_tag: Optional[str],
                  solver: str, dock_count: Optional[int], user: Optional[dict],
                  persist: bool = True, solver_params: Optional[dict] = None) -> dict:
    if scenario not in SCENARIO_MAP:
        raise ApiProblem(f'未知情景：{scenario}（可选 {"/".join(SCENARIO_MAP)}）')
    if scope not in ('姜蒜', '鲜面条', '全部'):
        raise ApiProblem('产品品类只能是 姜蒜 / 鲜面条 / 全部。')
    solver_scenario, default_fleet = SCENARIO_MAP[scenario]
    fleet_tag = fleet_tag or default_fleet
    known_fleets = {row['fleet_tag'] for row in conn.execute('SELECT DISTINCT fleet_tag FROM vehicles')}
    if fleet_tag not in known_fleets:
        raise ApiProblem(f'未知车队：{fleet_tag}（可选 {"/".join(sorted(known_fleets))}）')

    extra = solver_params or {}
    paper_kpi = None
    params = planning.load_params(conn)          # 两条求解路径共用
    products = planning.load_products(conn)
    if solver == 'alns':
        # 论文口径求解器：Clarke-Wright + ALNS + 车辆区间调度（移植自团队原始程序）
        profile = str(extra.get('profile') or 'case2')
        policy = str(extra.get('departure_policy') or 'ready')
        if profile not in ('case2', 'paper'):
            raise ApiProblem('算例口径只能是 case2（表2-3/2-4 真实窗口）或 paper（论文基准构造）。')
        if policy not in ('ready', 'optimise'):
            raise ApiProblem('发车策略只能是 ready（固定就绪时刻）或 optimise（窗口内优化）。')
        product_list = ('姜蒜', '鲜面条') if scope == '全部' else (scope,)
        try:
            plan = joint_plan.build_joint_plan(
                conn, fleet_tag=fleet_tag, products=product_list, profile=profile,
                departure_policy=policy,
                restarts=int(extra.get('restarts') or 5),
                iterations=int(extra.get('iterations') or 3000),
                dock_count=dock_count)
        except vrp_optimizer.VrpError as exc:
            raise ApiProblem(str(exc), status=400) from exc
        paper_kpi = plan['alns_summary']
        solution = {'schedule': plan['schedule'], 'solver': f'alns-{profile}',
                    'solver_status': 'ALNS', 'kpi': paper_kpi, 'notes': []}
        wait_min = WAIT_BY_SOLVER_SCENARIO.get(solver_scenario, 30)
        # ALNS 结果已是优化后口径，不再叠加 H0 的人工绕行系数
        SCENARIO_DISTANCE_FACTOR_OVERRIDE = 1.0
    else:
        try:
            plan = planning.build_plan(conn, scope, fleet_tag, dock_count=dock_count)
        except planning.PlanningError as exc:
            raise ApiProblem(str(exc), status=400) from exc

        params = planning.load_params(conn)
        products = planning.load_products(conn)
        wait_min = WAIT_BY_SOLVER_SCENARIO.get(solver_scenario, 30)
        runtime = {'solver': solver, 'wait_min': wait_min}
        runtime.update(extra)
        solution = solve_scenario(solver_scenario, plan['solver_data'], runtime)
        SCENARIO_DISTANCE_FACTOR_OVERRIDE = None

    # 情景口径：H0（人工调度基准）里程按 1.22 折算，其余情景为优化后口径
    # （与 simulation/scenarios.py 同源）。核算层必须同口径，否则 S0 与 S1 的结果会完全一样。
    # 注意：ALNS（论文口径）输出的已经是优化后里程，不能再叠加人工绕行系数。
    factor = (SCENARIO_DISTANCE_FACTOR_OVERRIDE if SCENARIO_DISTANCE_FACTOR_OVERRIDE is not None
              else SCENARIO_DISTANCE_FACTOR.get(solver_scenario, 1.0))
    if factor != 1.0:
        for trip in plan['trips']:
            trip['distance_km'] = round(trip['distance_km'] * factor, 2)
            trip['duration_min'] = round(trip['duration_min'] * factor, 1)
        plan['summary']['total_distance_km'] = round(sum(t['distance_km'] for t in plan['trips']), 2)
        for entry in solution.get('schedule', []):
            entry['distance_km'] = round(float(entry.get('distance_km') or 0) * factor, 2) \
                if entry.get('distance_km') else entry.get('distance_km')

    kpi = costing.price_plan(plan, products, params, solution.get('schedule'))
    if paper_kpi:
        # 论文 F1+F2 口径的日总配送成本，作为与论文对照的头条指标
        kpi['delivery_cost'] = paper_kpi['delivery_cost']
        kpi['delivery_cost_breakdown'] = {
            'fixed': paper_kpi['cost_fixed'], 'transport': paper_kpi['cost_transport'],
            'cool_delivery': paper_kpi['cost_cool_delivery'],
            'cool_service': paper_kpi['cost_cool_service'],
            'early': paper_kpi['cost_early'], 'late': paper_kpi['cost_late'],
        }
        kpi['on_time_rate'] = paper_kpi['on_time_rate']
        kpi['on_time_customers'] = paper_kpi['on_time_customers']
        kpi['customers'] = paper_kpi['customers']
        kpi['used_vehicles'] = paper_kpi['vehicles_used']
        kpi['trips'] = paper_kpi['trips']
        kpi['reused_trips'] = paper_kpi['reused_trips']
        kpi['total_distance_km'] = paper_kpi['total_distance_km']
        kpi['by_product'] = paper_kpi['by_product']

    # ⑤ 充电排班与校验、⑥ 月台协同（备货反推 / 预约 / 装车时序 / 冲突检测）
    dock_plan = docks.plan_docks(solution.get('schedule', []), trips_with_prep(plan), params,
                                 dock_count=dock_count or len(plan['solver_data'].get('docks') or []))
    charge_plan = charging.plan_charging(plan['vehicles'], solution.get('schedule', []), params)

    # 求解器口径的原始指标一并保留，便于与论文对照
    solver_kpi = solution.get('kpi') or {}
    run_id = None
    if persist:
        run_id = _store_run(conn, scenario, scope, fleet_tag, solver,
                            {'solver_scenario': solver_scenario, 'wait_min': wait_min,
                             'dock_count': dock_count}, plan, solution, kpi, user)

    return {
        'run_id': run_id,
        'scenario': scenario,
        'scenario_label': SCENARIO_LABELS.get(scenario, scenario),
        'solver_scenario': solver_scenario,
        'scope': scope,
        'fleet_tag': fleet_tag,
        'fleet_label': labels.fleet_label(fleet_tag),
        'profile': plan.get('profile'),
        'departure_policy': plan.get('departure_policy'),
        'solver': solution.get('solver'),
        'solver_label': labels.solver_label(solution.get('solver')),
        'solver_status': solution.get('solver_status'),
        'kpi': kpi,
        'solver_kpi': solver_kpi,
        'summary': plan['summary'],
        'dc': plan['dc'],
        'customers': plan['customers'],
        'trips': plan['trips'],
        'vehicles': plan['vehicles'],
        'schedule': solution.get('schedule', []),
        'charging_plan': charge_plan,
        'dock_plan': dock_plan,
        'warnings': plan['warnings'] + dock_plan['conflicts'] + charge_plan['conflicts'],
        'notes': solution.get('notes', []),
    }


def run_plan(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    scenario = str(payload.get('scenario') or 'S3').strip().upper()
    scope = str(payload.get('scope') or '姜蒜').strip()
    fleet = str(payload.get('fleet') or '').strip() or None
    solver = str(payload.get('solver') or 'auto').strip().lower()
    if solver not in ('auto', 'heuristic', 'cpsat', 'alns'):
        raise ApiProblem('求解器只能是 auto / heuristic / cpsat / alns（论文口径）。')
    profile = str(payload.get('profile') or 'case2').strip()
    departure_policy = str(payload.get('departure_policy') or 'ready').strip()
    restarts = payload.get('restarts')
    iterations = payload.get('iterations')
    dock_count = payload.get('dock_count')
    dock_count = int(dock_count) if str(dock_count or '').strip().isdigit() else None
    extra = {'profile': profile, 'departure_policy': departure_policy}
    if str(restarts or '').strip().isdigit():
        extra['restarts'] = max(1, min(10, int(restarts)))
    if str(iterations or '').strip().isdigit():
        extra['iterations'] = max(50, min(20000, int(iterations)))
    result = _run_scenario(conn, scenario, scope, fleet, solver, dock_count, user, solver_params=extra)
    return Response(200, result)


def compare_plan(conn: sqlite3.Connection, payload: dict, user: dict, settings=None) -> Response:
    """S0→S3 依次求解并给出边际效益与五维评分（不落库，便于反复对比）。

    四个情景**并行**求解（CP-SAT 是 C++ 实现，求解期间释放 GIL）。
    每个线程用独立数据库连接（sqlite3 连接不跨线程复用），
    并把 CP-SAT 时限收到 6 秒——对比只需要能反映口径差异的可行解，
    不必等每次求解都跑到最优，否则页面上点一次要等一分多钟。
    """
    scope = str(payload.get('scope') or '姜蒜').strip()
    solver = str(payload.get('solver') or 'auto').strip().lower()
    fleet = str(payload.get('fleet') or '').strip() or None
    scenarios = ('S0', 'S1', 'S2', 'S3')
    results: List[Optional[dict]] = [None] * len(scenarios)

    def work(index: int, name: str) -> None:
        own = db.connect(settings) if settings else conn
        try:
            results[index] = _run_scenario(own, name, scope, fleet, solver, None, user,
                                           persist=False, solver_params={'max_time_seconds': 6})
        finally:
            if own is not conn:
                own.close()

    with ThreadPoolExecutor(max_workers=len(scenarios)) as pool:
        futures = [pool.submit(work, index, name) for index, name in enumerate(scenarios)]
        for future in futures:
            future.result()          # 任一情景失败则整体失败，错误信息原样冒泡
    raw = [item for item in results if item]
    items = [{'scenario': item['scenario'], 'label': item['scenario_label'],
              'fleet_tag': item['fleet_tag'], 'fleet_label': item.get('fleet_label'),
              'solver': item['solver'], 'kpi': item['kpi'], 'summary': item['summary'],
              'dock': (item.get('dock_plan') or {}).get('summary'),
              'charge': (item.get('charging_plan') or {}).get('summary')} for item in raw]
    # 五维评分（经济/时效/绿色/协同/效率），以 S0 为 60 分锚点
    evaluation = scoring.score_all([{'scenario': item['scenario'], 'label': item['label'],
                                     'fleet_tag': item['fleet_tag'], 'solver': item['solver'],
                                     'kpi': item['kpi'], 'dock': item['dock']} for item in items])
    return Response(200, {'scope': scope, 'solver': solver,
                          'comparison': costing.compare_scenarios(raw),
                          'evaluation': evaluation,
                          'items': items})


def list_runs(conn: sqlite3.Connection, query: dict) -> Response:
    limit = min(int((query.get('limit') or ['20'])[0] or 20), 200)
    rows = conn.execute(
        '''SELECT id, scenario, scope, fleet_tag, solver, created_at, created_by, kpi_json
           FROM runs ORDER BY id DESC LIMIT ?''', (limit,))
    items = []
    for row in rows:
        item = dict(row)
        try:
            kpi = json.loads(item.pop('kpi_json') or '{}')
        except json.JSONDecodeError:
            kpi = {}
        item['kpi'] = {k: kpi.get(k) for k in ('total_cost', 'on_time_rate', 'carbon_kg',
                                               'trips', 'used_vehicles', 'avg_load_rate')}
        item['scenario_label'] = SCENARIO_LABELS.get(item['scenario'], item['scenario'])
        item['fleet_label'] = labels.fleet_label(item.get('fleet_tag'))
        item['solver_label'] = labels.solver_label(item.get('solver'))
        items.append(item)
    return Response(200, {'items': items, 'scenarios': SCENARIO_LABELS})


def get_run(conn: sqlite3.Connection, run_id: int) -> Response:
    row = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    if not row:
        raise ApiProblem(f'方案不存在：{run_id}', status=404)
    item = dict(row)
    try:
        payload = json.loads(item.pop('plan_json') or '{}')
    except json.JSONDecodeError:
        payload = {}
    try:
        item['kpi'] = json.loads(item.pop('kpi_json') or '{}')
    except json.JSONDecodeError:
        item['kpi'] = {}
    item['scenario_label'] = SCENARIO_LABELS.get(item['scenario'], item['scenario'])
    plan = payload.get('plan') or {}
    solution = payload.get('solution') or {}
    params = planning.load_params(conn)
    dock_plan = docks.plan_docks(solution.get('schedule', []), trips_with_prep(plan), params,
                                 dock_count=len(plan.get('solver_data', {}).get('docks') or []))
    charge_plan = charging.plan_charging(plan.get('vehicles') or [], solution.get('schedule', []), params)
    return Response(200, {
        'run_id': item.get('id'),
        **item,
        'summary': plan.get('summary'),
        'dc': plan.get('dc'),
        'customers': plan.get('customers'),
        'trips': plan.get('trips'),
        'vehicles': plan.get('vehicles'),
        'schedule': solution.get('schedule', []),
        'charging_plan': charge_plan,
        'dock_plan': dock_plan,
        'warnings': (plan.get('warnings') or []) + dock_plan['conflicts'] + charge_plan['conflicts'],
        'solver_kpi': solution.get('kpi'),
        'notes': solution.get('notes', []),
    })


def export_run(conn: sqlite3.Connection, run_id: int, kind: str) -> Response:
    """导出 KPI 报表（CSV 文本，带 BOM，Excel 直接打开不乱码）。

    ``kind = trip`` 出趟次明细（含成本分项与碳排），``kind = kpi`` 出方案级 KPI 汇总。
    返回 JSON 包装的文本，由前端触发浏览器下载——避免改动 JSON 传输契约。
    """
    if kind not in ('trip', 'kpi'):
        raise ApiProblem('导出类型只能是 trip（趟次明细）或 kpi（KPI 汇总）。')
    detail = get_run(conn, run_id).payload
    kpi = detail.get('kpi') or {}
    trip_costs = {str(t.get('trip_id')): t for t in (kpi.get('trip_details') or [])}
    schedule = {str(s.get('trip_id')): s for s in (detail.get('schedule') or [])}
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if kind == 'trip':
        writer.writerow(['趟次', '批次', '品类', '车辆', '车型', '能源', '客户顺序', '装载kg', '体积m3',
                         '装载率%', '里程km', '时长min', '发车时刻', '月台', '装车时段',
                         '运输成本', '制冷成本', '能耗成本', '货损成本', '惩罚成本', '维护成本',
                         '合计成本', '碳排kg', '能耗'])
        for trip in detail.get('trips') or []:
            trip_id = str(trip.get('trip_id'))
            cost = trip_costs.get(trip_id, {})
            entry = schedule.get(trip_id, {})
            load_window = ''
            if entry.get('load_start') is not None:
                load_window = f"{db.to_hhmm(entry['load_start'])}-{db.to_hhmm(entry['load_end'])}"
            writer.writerow([
                trip_id, trip.get('batch_id'), trip.get('product'), trip.get('vehicle_code'),
                trip.get('vehicle_type'), trip.get('energy_type'),
                ' → '.join(str(c) for c in (trip.get('customer_codes') or [])),
                trip.get('load_kg'), trip.get('volume_m3'), trip.get('load_rate'),
                trip.get('distance_km'), round(float(trip.get('duration_min') or 0), 1),
                db.to_hhmm(trip.get('planned_departure_min') or 0), entry.get('dock_id') or '',
                load_window, cost.get('transport_cost'), cost.get('reefer_cost'),
                cost.get('energy_cost'), cost.get('loss_cost'), cost.get('penalty_cost'),
                cost.get('maintenance_cost'), cost.get('total_cost'), cost.get('carbon_kg'),
                f"{cost.get('energy_use')} {cost.get('energy_unit')}" if cost.get('energy_use') else '',
            ])
    else:
        docks = (detail.get('dock_plan') or {}).get('summary') or {}
        charging = (detail.get('charging_plan') or {}).get('summary') or {}
        rows = [
            ('方案编号', detail.get('run_id'), ''),
            ('情景', f"{detail.get('scenario')} {detail.get('scenario_label') or ''}", '论文 4.5.4.2'),
            ('品类 / 车队', f"{detail.get('scope')} / {detail.get('fleet_tag')}", ''),
            ('求解器', detail.get('solver'), ''),
            ('生成时间', detail.get('created_at'), ''),
            ('日总成本(元)', kpi.get('total_cost'), '表2-5/2-6 口径合计'),
            ('运输成本(元)', kpi.get('transport_cost'), '出车固定成本 + 里程成本'),
            ('制冷成本(元)', kpi.get('reefer_cost'), '配送 8.5 元/h + 服务 10 元/h'),
            ('时间窗惩罚(元)', kpi.get('penalty_cost'), '早到 5 元/h + 晚到 15 元/h'),
            ('货损成本(元)', kpi.get('loss_cost'), '配送 0.008 + 服务 0.0016 × 货值'),
            ('能耗成本(元)', kpi.get('energy_cost'), '谷段电价 0.256 元/kWh、柴油 6.5 元/L'),
            ('维护成本(元)', kpi.get('maintenance_cost'), '按车型'),
            ('碳排放(kg)', kpi.get('carbon_kg'), '燃油 2.63 kg/L、电网 0.1564 kg/kWh'),
            ('总里程(km)', kpi.get('total_distance_km'), 'Haversine R=6371.0 km'),
            ('趟次 / 用车', f"{kpi.get('trips')} / {kpi.get('used_vehicles')}", ''),
            ('平均装载率(%)', kpi.get('avg_load_rate'), '载重口径'),
            ('车辆利用率(%)', kpi.get('vehicle_utilization'), '用车数 / 车队规模'),
            ('准时率(%)', kpi.get('on_time_rate'), f"{kpi.get('on_time_customers')}/{kpi.get('customers')} 客户"),
            ('早到 / 晚到(h)', f"{kpi.get('early_hours')} / {kpi.get('late_hours')}", ''),
            ('服务客户数', kpi.get('customers'), ''),
            ('月台装车任务', docks.get('trips'), f"使用 {docks.get('docks_used')} 个月台"),
            ('月台冲突', docks.get('conflicts'), f"严重 {docks.get('errors')} / 提示 {docks.get('warnings')}"),
            ('平均月台等待(分)', docks.get('avg_wait_min'), ''),
            ('充电次数 / 电量', f"{charging.get('sessions')} / {charging.get('total_kwh')} kWh", '谷段 01:00-07:00'),
            ('充电费用(元)', charging.get('total_cost'), '3 × 120 kW'),
            ('充电碳排(kg)', charging.get('total_carbon'), ''),
        ]
        writer.writerow(['指标', '数值', '口径'])
        writer.writerows(rows)

    filename = f"plan{run_id}_{'trips' if kind == 'trip' else 'kpi'}.csv"
    return Response(200, {'filename': filename, 'kind': kind, 'run_id': run_id,
                          'content': '\ufeff' + buffer.getvalue()})


def fleet_evaluate(conn: sqlite3.Connection, payload: dict, user: dict) -> Response:
    """第三章口径：C0 / C3 / C4 车队配置的日全成本与碳排对比。

    每个车队各跑一次 ALNS（默认快速档），再把该车队的当日作业量代入
    「折旧 + 能源 + 维护 + 年费」四项口径，并给出碳排与相对 C0 的减排比例。
    """
    profile = str(payload.get('profile') or 'paper').strip()
    restarts = max(1, min(5, int(payload.get('restarts') or 1)))
    iterations = max(100, min(5000, int(payload.get('iterations') or 400)))
    names = payload.get('scenarios') or ['C0', 'C3', 'C4']
    reoptimize = bool(payload.get('reoptimize'))
    params = planning.load_params(conn)
    products = planning.load_products(conn)

    # 第三章口径的关键：**同一组配送任务、只换车队配置**，否则各配置各跑一次 ALNS，
    # 差异会混入随机性（曾出现 8 纯电反而比 6 纯电碳排高）。
    route_plan = None
    if not reoptimize:
        try:
            route_plan = joint_plan.build_joint_plan(
                conn, fleet_tag=lifecycle.COMPARE_SCENARIOS['C0']['fleet_tag'], profile=profile,
                restarts=restarts, iterations=iterations)
        except vrp_optimizer.VrpError as exc:
            raise ApiProblem(f'生成基准路线失败：{exc}', status=400) from exc

    items = []
    for name in names:
        spec = lifecycle.COMPARE_SCENARIOS.get(str(name))
        if not spec:
            raise ApiProblem(f'未知配置：{name}（可选 {"/".join(lifecycle.COMPARE_SCENARIOS)}）')
        vehicles = [dict(row) for row in conn.execute(
            'SELECT code, vehicle_type, energy_type, capacity_kg, capacity_m3, fixed_cost, unit_cost, '
            '       purchase_price_wan, maintenance_cost '
            'FROM vehicles WHERE fleet_tag = ? AND available = 1 '
            "ORDER BY vehicle_type, CASE energy_type WHEN '纯电' THEN 0 ELSE 1 END, code",
            (spec['fleet_tag'],))]
        if not vehicles:
            raise ApiProblem(f'车队 {spec["fleet_tag"]} 下没有可用车辆。')
        if route_plan is None:
            try:
                plan = joint_plan.build_joint_plan(conn, fleet_tag=spec['fleet_tag'], profile=profile,
                                                   restarts=restarts, iterations=iterations)
            except vrp_optimizer.VrpError as exc:
                raise ApiProblem(f'{name}：{exc}', status=400) from exc
            evaluation = lifecycle.evaluate_fleet(
                vehicles, lifecycle.workload_from_trips(plan['trips']), params)
        else:
            plan = route_plan
            evaluation = lifecycle.evaluate_fleet_on_routes(plan['trips'], vehicles, params)
            if evaluation is None:
                raise ApiProblem(f'{name} 运力不足：该车队无法完成这 {len(plan["trips"])} 趟任务。', status=400)
        items.append({
            'scenario': name, 'label': spec['label'], 'fleet_tag': spec['fleet_tag'],
            'vehicles': len(vehicles),
            'ev_count': sum(1 for v in vehicles if v['energy_type'] == '纯电'),
            'used_vehicles': evaluation.get('used_vehicles') or len(vehicles),
            'trips': plan['alns_summary']['trips'],
            'distance_km': plan['alns_summary']['total_distance_km'],
            'delivery_cost': plan['alns_summary']['delivery_cost'],
            'daily_cost': evaluation['daily_cost'],
            'breakdown': evaluation['breakdown'],
            'carbon_kg': evaluation['carbon_kg'],
            'by_energy': evaluation['by_energy'],
            'on_time_rate': plan['alns_summary']['on_time_rate'],
            'vehicle_detail': evaluation['vehicles'],
            'assignment': evaluation.get('assignment'),
        })

    base = next((item for item in items if item['scenario'] == 'C0'), items[0] if items else None)
    if base:
        for item in items:
            item['cost_delta'] = round(item['daily_cost'] - base['daily_cost'], 2)
            item['cost_delta_pct'] = round((item['daily_cost'] - base['daily_cost']) / base['daily_cost'] * 100, 1) \
                if base['daily_cost'] else 0.0
            item['carbon_cut_pct'] = round((base['carbon_kg'] - item['carbon_kg']) / base['carbon_kg'] * 100, 1) \
                if base['carbon_kg'] else 0.0
    best = min(items, key=lambda i: i['daily_cost']) if items else None
    return Response(200, {
        'profile': profile, 'scope': '全部', 'items': items,
        'best_cost': best['scenario'] if best else None,
        'lowest_carbon': min(items, key=lambda i: i['carbon_kg'])['scenario'] if items else None,
        'calibration_note': lifecycle.CALIBRATION_NOTE,
    })


def handle(method: str, target: str, body: bytes, query: dict, headers: dict,
           settings: Optional[Settings] = None) -> Optional[Response]:
    if not target.startswith('/api/plan'):
        return None
    settings = settings or get_settings()
    conn = db.connect(settings)
    try:
        user = _current_user(conn, headers)
        path = target.split('?', 1)[0].rstrip('/')
        segments = [seg for seg in path.split('/') if seg][2:]   # 去掉 api / plan

        if method in ('GET', 'HEAD') and len(segments) == 3 and segments[0] == 'runs' \
                and segments[1].isdigit() and segments[2] == 'export':
            require(user, 'plan.view')
            kind = (query.get('kind') or ['trip'])[0]
            return export_run(conn, int(segments[1]), kind)
        if method == 'POST' and segments[:1] == ['run']:
            require(user, 'plan.run')
            return run_plan(conn, _body(body), user)
        if method == 'POST' and segments[:1] == ['fleet-eval']:
            require(user, 'scenarios.run')
            return fleet_evaluate(conn, _body(body), user)
        if method == 'POST' and segments[:1] == ['compare']:
            require(user, 'scenarios.run')
            return compare_plan(conn, _body(body), user, settings)
        if method in ('GET', 'HEAD'):
            if segments[:1] == ['runs'] and len(segments) == 2 and segments[1].isdigit():
                require(user, 'plan.view')
                return get_run(conn, int(segments[1]))
            if segments[:1] == ['runs']:
                require(user, 'plan.view')
                return list_runs(conn, query)
            if segments[:1] == ['latest']:
                require(user, 'plan.view')
                row = conn.execute('SELECT MAX(id) AS id FROM runs').fetchone()
                if not row or not row['id']:
                    return Response(200, {'run': None})
                return get_run(conn, int(row['id']))
        raise ApiProblem('不支持的方案接口。', status=404)
    finally:
        conn.close()


__all__ = ['SCENARIO_LABELS', 'SCENARIO_MAP', 'compare_plan', 'get_run', 'handle',
           'list_runs', 'run_plan']
