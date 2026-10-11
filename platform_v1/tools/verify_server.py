"""对已部署的服务器做端到端验收（纯 HTTP，不需要 SSH）。

用法：
    python verify_server.py [http://139.155.98.122] [admin] [admin123]

会依次验证：静态页面版本、登录与会话、客户/车辆/参数/留痕接口、
距离矩阵校核、方案生成（S3）、S0→S3 五维评价、月台与充电、监控快照、CSV 导出。
口令若已被改过，请把新口令作为第 3 个参数传入。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://139.155.98.122').rstrip('/')
USER = sys.argv[2] if len(sys.argv) > 2 else 'admin'
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else 'admin123'
COOKIE = {'value': None}
PASSED, FAILED = [], []


def call(method, path, payload=None, raw=False, timeout=180):
    url = BASE + path
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header('Content-Type', 'application/json')
    if COOKIE['value']:
        request.add_header('Cookie', COOKIE['value'])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get('Set-Cookie'):
                COOKIE['value'] = response.headers['Set-Cookie'].split(';')[0]
            if raw:
                return response.status, body
            return response.status, json.loads(body.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body
        try:
            return exc.code, json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            return exc.code, {'error': body[:200].decode('utf-8', 'replace')}
    except Exception as exc:                                  # noqa: BLE001
        return 0, {'error': f'{type(exc).__name__}: {exc}'}


def check(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(('  PASS  ' if ok else '  FAIL  ') + name + ('  ' + str(detail) if detail else ''))


def main():
    print(f'验收目标：{BASE}\n')

    print('[1] 静态资源与接口版本')
    for path, expect_bytes in (('/login.html', 5249), ('/plan.html', 24325), ('/customers.html', None),
                               ('/scenarios.html', None), ('/monitor.html', None), ('/admin.css', None)):
        status, body = call('GET', path, raw=True)
        check(f'GET {path}', status == 200, f'{status} {len(body)} B')
    status, payload = call('GET', '/api/auth/me')
    check('新接口 /api/auth/me 已生效（未登录应 401，不是 404）', status == 401, f'{status} {payload}')

    print('\n[2] 登录与会话')
    status, payload = call('POST', '/api/auth/login', {'username': USER, 'password': PASSWORD})
    check('登录成功', status == 200, payload.get('error', '') if status != 200 else '')
    if status != 200:
        print('\n登录失败，后续跳过。若口令已改，请用正确的第 3 个参数重跑。')
        return 1
    check('会话 Cookie 已下发', bool(COOKIE['value']))
    user = payload.get('user') or {}
    check('当前用户信息', bool(user.get('username')), f"{user.get('username')} / {user.get('role')}")
    if user.get('must_change_password'):
        print('  注意：该账号仍处于「首次登录必须改口令」状态')

    status, payload = call('GET', '/api/auth/me')
    check('带会话访问 /api/auth/me', status == 200)

    print('\n[3] 数据层')
    status, payload = call('GET', '/api/customers?limit=100')
    items = (payload or {}).get('items') or []
    check('客户列表 60 条', status == 200 and len(items) == 60, f'{status} {len(items)} 条')
    status, payload = call('GET', '/api/vehicles')
    vehicles = (payload or {}).get('items') or []
    check('车辆列表 28 辆（三种车队口径）', status == 200 and len(vehicles) == 28, f'{status} {len(vehicles)} 辆')
    status, payload = call('GET', '/api/params')
    params = (payload or {}).get('items') or (payload or {}).get('params') or []
    check('参数表可读', status == 200 and len(params) > 20, f'{status} {len(params)} 项')
    status, payload = call('GET', '/api/geo/check')
    data = payload or {}
    consistent = data.get('consistent')
    if consistent is None and isinstance(data.get('scopes'), dict):
        consistent = all(bool(v.get('consistent')) for v in data['scopes'].values())
    check('Haversine 距离矩阵与团队既有矩阵逐格一致', status == 200 and consistent is True,
          f'{status} {json.dumps(data, ensure_ascii=False)[:160]}')

    print('\n[4] 方案生成（S3 / 姜蒜 / 自动求解器）')
    started = time.time()
    status, payload = call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜', 'solver': 'auto'})
    elapsed = time.time() - started
    ok = status == 200
    check('生成方案', ok, f'{status} 用时 {elapsed:.1f}s ' + (payload.get('error', '') if not ok else ''))
    run_id = None
    if ok:
        run_id = payload['run_id']
        kpi = payload['kpi']
        check('趟次与调度条目一致', len(payload['trips']) == len(payload['schedule']),
              f"{len(payload['trips'])} 趟次")
        check('求解器与车队', bool(payload.get('solver')), f"{payload['solver']} / {payload.get('fleet_tag')}")
        check('KPI 关键指标齐全', all(k in kpi for k in
              ('total_cost', 'on_time_rate', 'carbon_kg', 'vehicle_utilization', 'cost_breakdown')))
        print(f"       总成本 {kpi['total_cost']} 元 · 准时率 {kpi['on_time_rate']}% · "
              f"里程 {kpi['total_distance_km']} km · 碳排 {kpi['carbon_kg']} kg")
        dock = (payload.get('dock_plan') or {}).get('summary') or {}
        charge = (payload.get('charging_plan') or {}).get('summary') or {}
        check('月台协同数据', bool(dock), f"{dock.get('trips')} 任务 / {dock.get('conflicts')} 冲突")
        check('充电排班数据（C3 纯电车）', charge.get('sessions', 0) > 0,
              f"{charge.get('sessions')} 次 / {charge.get('total_kwh')} kWh / {charge.get('total_cost')} 元")
        sessions = (payload.get('charging_plan') or {}).get('sessions') or []
        in_valley = all(s.get('in_valley') for s in sessions)
        check('全部充电时段落在谷段 01:00-07:00', in_valley)

    print('\n[5] S0→S3 多情景评价')
    started = time.time()
    status, payload = call('POST', '/api/plan/compare', {'scope': '姜蒜', 'solver': 'auto'})
    elapsed = time.time() - started
    ok = status == 200
    check('四情景对比', ok, f'{status} 用时 {elapsed:.1f}s')
    if ok:
        evaluation = payload.get('evaluation') or {}
        items = evaluation.get('items') or []
        check('五维评分齐全', evaluation.get('dimensions') == ['经济', '时效', '绿色', '协同', '效率'],
              str(evaluation.get('dimensions')))
        check('四个情景都有结果', len(items) == 4, str(evaluation.get('ranking')))
        if items:
            base = items[0]
            check('S0 各维均为 60 分锚点', all(abs(v - 60.0) < 0.01 for v in base['scores'].values()),
                  json.dumps(base['scores'], ensure_ascii=False))
            for row in items:
                print(f"       {row['scenario']} 总分 {row['total_score']} 成本 {row['metrics']['total_cost']} 元 "
                      f"碳排 {row['metrics']['carbon_kg']} kg 准时 {row['metrics']['on_time_rate']}%")

    print('\n[6] 监控反馈')
    status, payload = call('GET', '/api/monitor/state?tick=420')
    ok = status == 200
    check('监控快照', ok, f'{status} ' + (payload.get('error', '') if not ok else ''))
    if ok:
        summary = payload['summary']
        check('车辆状态与预警分级', bool(summary.get('status')) and bool(summary.get('alerts')),
              f"最差状态 {summary.get('worst_status')} · 预警 {summary.get('alerts')}")
        vehicle = (payload.get('vehicles') or [{}])[0]
        check('车辆含位置/厢温/电量', vehicle.get('lon') is not None and vehicle.get('temperature') is not None,
              f"{vehicle.get('vehicle_code')} {vehicle.get('temperature')}℃ {vehicle.get('energy')}")
        status2, again = call('GET', '/api/monitor/state?tick=420')
        check('同一时刻快照可复现', status2 == 200 and again['vehicles'] == payload['vehicles'])

    print('\n[7] CSV 报表导出')
    if run_id:
        status, payload = call('GET', f'/api/plan/runs/{run_id}/export?kind=trip')
        ok = status == 200
        check('趟次明细 CSV', ok and payload.get('content', '').startswith('\ufeff'), f'{status}')
        if ok:
            lines = [line for line in payload['content'].splitlines() if line.strip()]
            print(f"       {payload['filename']}：{len(lines)} 行（含表头）")
        status, payload = call('GET', f'/api/plan/runs/{run_id}/export?kind=kpi')
        check('KPI 汇总 CSV', status == 200 and '日总成本(元)' in payload.get('content', ''))

    print('\n[8] 权限')
    status, payload = call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'})
    check('已登录可重复生成方案', status == 200, str(status))
    saved = COOKIE['value']
    COOKIE['value'] = None
    status, payload = call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'})
    check('未登录被拒（401）', status == 401, str(status))
    COOKIE['value'] = saved

    print('\n[9] 留痕')
    status, payload = call('GET', '/api/audit?limit=10')
    items = (payload or {}).get('items') or []
    actions = {item.get('action') for item in items}
    check('操作留痕已记录', status == 200 and len(items) > 0, f'{len(items)} 条，含 {sorted(a for a in actions if a)}')
    check('包含方案生成留痕', 'plan.run' in actions, str(sorted(actions)))

    print(f'\n===== 验收结果：{len(PASSED)} 项通过，{len(FAILED)} 项失败 =====')
    for name in FAILED:
        print('  失败：' + name)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
