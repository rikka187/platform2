"""服务器端验收：论文口径 ALNS（S0 复现）+ 第三章 C0/C3/C4 车队对比。

用法：python tools/server_acceptance.py [base_url] [user] [password]
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://139.155.98.122').rstrip('/')
USER = sys.argv[2] if len(sys.argv) > 2 else 'admin'
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else 'Yinli2026'
COOKIE = {'value': None}


def call(method, path, payload=None, timeout=900):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header('Content-Type', 'application/json')
    if COOKIE['value']:
        request.add_header('Cookie', COOKIE['value'])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode('utf-8')
            if response.headers.get('Set-Cookie'):
                COOKIE['value'] = response.headers['Set-Cookie'].split(';')[0]
            return response.status, json.loads(body or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or '{}')
        except json.JSONDecodeError:
            return exc.code, {}
    except Exception as exc:                                     # noqa: BLE001
        return 0, {'error': f'{type(exc).__name__}: {exc}'}


print(f'验收目标 {BASE}')
status, body = call('POST', '/api/auth/login', {'username': USER, 'password': PASSWORD})
print(f'登录：HTTP {status}' + ('' if status == 200 else f' {body.get("error", "")[:80]}'))
if status != 200:
    sys.exit(1)

print('\n[1] 论文口径 ALNS（全部品类联合、论文基准算例）')
started = time.time()
status, data = call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '全部', 'solver': 'alns',
                                              'profile': 'paper', 'fleet': 'baseline',
                                              'restarts': 3, 'iterations': 1500})
elapsed = time.time() - started
if status != 200:
    print(f'  失败：HTTP {status} {data.get("error", "")[:200]}')
else:
    kpi = data['kpi']
    print(f"  用时 {elapsed:.0f} s｜求解器 {data.get('solver')}｜算例口径 {data.get('profile')}"
          f"｜发车策略 {data.get('departure_policy')}")
    print(f"  趟次 {kpi.get('trips')}｜用车 {kpi.get('used_vehicles')}（复用 {kpi.get('reused_trips')} 趟）"
          f"｜里程 {kpi.get('total_distance_km')} km｜准时率 {kpi.get('on_time_rate')}%")
    print(f"  论文口径 日总配送成本 {kpi.get('delivery_cost')} 元 = {kpi.get('delivery_cost_breakdown')}")
    print(f"  扩展口径 总成本 {kpi.get('total_cost')} 元｜碳排 {kpi.get('carbon_kg')} kg")
    print(f"  分品类：{json.dumps(kpi.get('by_product'), ensure_ascii=False)}")
    print('\n  与论文 S0 对照（8 趟次 / 7 辆 / 717.16 km / 4226.68 元 / 75.0%）：')
    for label, actual, target in (('趟次', kpi.get('trips'), 8), ('用车', kpi.get('used_vehicles'), 7),
                                  ('里程 km', kpi.get('total_distance_km'), 717.16),
                                  ('日总配送成本 元', kpi.get('delivery_cost'), 4226.68),
                                  ('准时率 %', kpi.get('on_time_rate'), 75.0)):
        if actual is None:
            continue
        delta = actual - target
        pct = (delta / target * 100) if target else 0
        print(f'    {label:16s} 本平台 {actual:>9} ｜ 论文 {target:>9} ｜ 差 {delta:+.2f}（{pct:+.1f}%）')

print('\n[2] 第三章口径 C0 / C3 / C4（同一组任务、只换车队配置）')
started = time.time()
status, data = call('POST', '/api/plan/fleet-eval', {'profile': 'paper', 'restarts': 1,
                                                     'iterations': 400,
                                                     'scenarios': ['C0', 'C3', 'C4']})
elapsed = time.time() - started
if status != 200:
    print(f'  失败：HTTP {status} {data.get("error", "")[:200]}')
else:
    print(f"  用时 {elapsed:.0f} s｜最低成本 {data.get('best_cost')}｜最低碳排 {data.get('lowest_carbon')}")
    for item in data['items']:
        print(f"  {item['scenario']} {item['label']}")
        print(f"      {item['vehicles']} 辆（纯电 {item['ev_count']}）｜{item['trips']} 趟｜"
              f"{item['distance_km']} km｜用车 {item.get('used_vehicles')}")
        print(f"      日全成本 {item['daily_cost']} 元（{item['cost_delta_pct']:+.1f}% vs C0）"
              f" = 折旧 {item['breakdown']['amortization']} + 能源 {item['breakdown']['energy_cost']}"
              f" + 维护 {item['breakdown']['maintenance']} + 年费 {item['breakdown']['fixed_annual']}")
        print(f"      碳排 {item['carbon_kg']} kg（减排 {item['carbon_cut_pct']}%）")

print('\n[3] 监控与历史')
status, latest = call('GET', '/api/plan/latest')
if status == 200 and latest.get('run_id'):
    kpi = latest.get('kpi') or {}
    print(f"  最近方案 #{latest['run_id']}｜{latest.get('scenario')}｜{latest.get('scope')}"
          f"｜成本 {kpi.get('total_cost')} 元")
    status, snap = call('GET', f"/api/monitor/state?run_id={latest['run_id']}&tick=420")
    if status == 200:
        print(f"  监控快照：在途 {snap['summary']['in_transit']}｜最差状态 {snap['summary']['worst_status']}"
              f"｜预警 {snap['summary']['alerts']}")
