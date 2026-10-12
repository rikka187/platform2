"""服务器中文标签验收：参数页/车辆页/留痕页/方案页。"""
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://139.155.98.122').rstrip('/')
COOKIE = {'value': None}


def call(method, path, payload=None, timeout=120, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header('Content-Type', 'application/json')
    if COOKIE['value']:
        request.add_header('Cookie', COOKIE['value'])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get('Set-Cookie'):
                COOKIE['value'] = response.headers['Set-Cookie'].split(';')[0]
            return response.status, (body if raw else json.loads(body.decode() or '{}'))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body
        try:
            return exc.code, json.loads(body.decode() or '{}')
        except json.JSONDecodeError:
            return exc.code, {}
    except Exception as exc:                                     # noqa: BLE001
        return 0, {'error': f'{type(exc).__name__}: {exc}'}


print(f'验收目标 {BASE}')
status, body = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'Yinli2026'})
print(f'登录 HTTP {status}' + ('' if status == 200 else f" {body.get('error', '')[:70]}"))
if status != 200:
    sys.exit(1)

status, params = call('GET', '/api/params')
if status != 200 or 'groups' not in params:
    print(f"[1] 参数页：尚未生效（HTTP {status}，缺 groups 字段）→ 需要重启服务")
else:
    print(f"[1] 参数页：{params['count']} 项，分 {len(params['groups'])} 组：" +
          '／'.join(g['title'] for g in params['groups']))
    for item in params['items'][:4]:
        print(f"      {item['key']:24s} → {item['label']}（{item['unit']}）｜{item['group']}"
              f"｜{item['note'][:22]}")

status, vehicles = call('GET', '/api/vehicles')
first = (vehicles.get('items') or [{}])[0]
print(f"[2] 车辆页：{len(vehicles.get('items') or [])} 辆｜车号 {first.get('code')} "
      f"车队字段 {first.get('fleet_tag')} → 显示 {first.get('fleet_label', '（未生效）')}")
print(f"      车队列表：{'、'.join(f['label'] for f in vehicles.get('fleets', []))}"
      if vehicles.get('fleets') else '      车队列表：未生效')

status, audit = call('GET', '/api/audit?limit=4')
rows = audit.get('items') or []
print('[3] 留痕页：' + '｜'.join(f"{r.get('action')} → {r.get('action_label', '（未生效）')}" for r in rows))

status, runs = call('GET', '/api/plan/runs?limit=2')
items = runs.get('items') or []
print('[4] 方案历史：' + ('｜'.join(
    f"#{r['id']} {r.get('scenario')} {r.get('fleet_label', r.get('fleet_tag'))} "
    f"{r.get('solver_label', r.get('solver'))}" for r in items) if items else '无记录'))

status, html = call('GET', '/plan.html', raw=True)
text = html.decode('utf-8', 'replace') if isinstance(html, bytes) else ''
print(f"[5] plan.html：{len(text)} B｜求解器中文标签 "
      f"{'已生效' if 'solver_label' in text else '未生效'}｜车队中文 "
      f"{'已生效' if 'fleet_label' in text else '未生效'}")
for name in ('/params.html', '/vehicles.html', '/users.html'):
    status, page = call('GET', name, raw=True)
    body_text = page.decode('utf-8', 'replace') if isinstance(page, bytes) else ''
    mark = {'/params.html': 'item.label || item.key', '/vehicles.html': 'fleet_label',
            '/users.html': 'action_label'}[name]
    print(f"      {name}: {status} {len(body_text)} B｜{'已生效' if mark in body_text else '未生效'}")
