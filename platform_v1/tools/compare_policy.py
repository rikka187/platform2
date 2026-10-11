"""对比发车时刻策略：ready（与原程序一致）vs optimise（论文 o_vk 的完整实现）。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://139.155.98.122'
COOKIE = {'value': None}


def call(method, path, payload=None, timeout=900):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header('Content-Type', 'application/json')
    if COOKIE['value']:
        request.add_header('Cookie', COOKIE['value'])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.headers.get('Set-Cookie'):
                COOKIE['value'] = response.headers['Set-Cookie'].split(';')[0]
            return response.status, json.loads(response.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or '{}')
        except json.JSONDecodeError:
            return exc.code, {}


status, _ = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'Yinli2026'})
print('登录:', status)
for policy in ('ready', 'optimise'):
    started = time.time()
    status, data = call('POST', '/api/plan/run', {
        'scenario': 'S1', 'scope': '全部', 'solver': 'alns', 'profile': 'paper',
        'fleet': 'baseline', 'departure_policy': policy, 'restarts': 3, 'iterations': 1500})
    if status != 200:
        print(policy, '失败', status, str(data)[:150])
        continue
    kpi = data['kpi']
    breakdown = kpi.get('delivery_cost_breakdown') or {}
    print(f"\n发车策略 {policy}（{time.time() - started:.0f} s）")
    print(f"  趟次 {kpi['trips']}｜用车 {kpi['used_vehicles']}（复用 {kpi.get('reused_trips')}）"
          f"｜里程 {kpi['total_distance_km']} km｜准时率 {kpi['on_time_rate']}%")
    print(f"  日总配送成本 {kpi['delivery_cost']} 元 = {breakdown}")
    print(f"  其中 早到 {breakdown.get('early')} 元 / 晚到 {breakdown.get('late')} 元")
