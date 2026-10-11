"""WSGI 部署路径的端到端验收（gunicorn / 宝塔「Python 项目」用的就是这条路径）。

与 test_wsgi.py 的单点检查不同，这里走完整业务流：登录 → 数据 → 方案 → 导出 → 监控，
并覆盖 Cookie 传递、静态文件、以及 PATCH/DELETE 这类容易被 WSGI 适配层吃掉的方法。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在导入 wsgi 之前设定，wsgi 导入时会做一次启动初始化（建库 + 种子数据）
_TMP = tempfile.TemporaryDirectory()
os.environ['PLATFORM_ROOT'] = str(ROOT)
os.environ['PLATFORM_DB'] = str(Path(_TMP.name) / 'platform.db')
os.environ['PLATFORM_ACCESS_LOG'] = '0'
os.environ.setdefault('PLATFORM_COOKIE_SECURE', '0')

import wsgi  # noqa: E402


class WsgiHarness:
    """最小 WSGI 客户端：负责组 environ、收集响应、维护 Cookie。"""

    def __init__(self):
        self.cookies = {}

    def call(self, method, path, payload=None, headers=None):
        query = ''
        if '?' in path:
            path, _, query = path.partition('?')
        body = json.dumps(payload).encode('utf-8') if payload is not None else b''
        environ = {
            'REQUEST_METHOD': method,
            'PATH_INFO': path,
            'QUERY_STRING': query,
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '80',
            'SERVER_PROTOCOL': 'HTTP/1.1',
            'wsgi.version': (1, 0),
            'wsgi.url_scheme': 'http',
            'wsgi.input': __import__('io').BytesIO(body),
            'wsgi.errors': sys.stderr,
            'wsgi.multithread': False,
            'wsgi.multiprocess': False,
            'wsgi.run_once': False,
            'CONTENT_LENGTH': str(len(body)),
            'CONTENT_TYPE': 'application/json',
            'HTTP_HOST': 'localhost',
        }
        if self.cookies:
            environ['HTTP_COOKIE'] = '; '.join(f'{k}={v}' for k, v in self.cookies.items())
        for key, value in (headers or {}).items():
            environ['HTTP_' + key.upper().replace('-', '_')] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured['status'] = int(status.split()[0])
            captured['headers'] = response_headers

        chunks = wsgi.application(environ, start_response)
        raw = b''.join(chunks)
        for name, value in captured.get('headers', []):
            if name.lower() == 'set-cookie':
                pair = value.split(';')[0]
                key, _, val = pair.partition('=')
                self.cookies[key.strip()] = val
        try:
            parsed = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        return captured.get('status'), dict(captured.get('headers', [])), parsed, raw


class TestWsgiEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = WsgiHarness()

    def test_01_static_pages_served(self):
        for page in ('/login.html', '/plan.html', '/customers.html', '/scenarios.html', '/monitor.html'):
            status, headers, _, raw = self.client.call('GET', page)
            self.assertEqual(status, 200, page)
            self.assertIn('text/html', headers.get('Content-Type', ''), page)
            self.assertGreater(len(raw), 500, page)

    def test_02_unknown_api_404_and_static_traversal_blocked(self):
        status, _, payload, _ = self.client.call('GET', '/api/definitely-not-here')
        self.assertEqual(status, 404)
        self.assertIn('error', payload or {})
        status, _, _, _ = self.client.call('GET', '/../server.py')
        self.assertEqual(status, 404)

    def test_03_login_sets_cookie_and_me_works(self):
        status, headers, payload, _ = self.client.call(
            'POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
        self.assertEqual(status, 200, payload)
        self.assertIn('platform_session', self.client.cookies)
        self.assertIn('HttpOnly', headers.get('Set-Cookie', ''))
        status, _, payload, _ = self.client.call('GET', '/api/auth/me')
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload['user']['username'], 'admin')

    def test_04_seeded_data_through_wsgi(self):
        status, _, payload, _ = self.client.call('GET', '/api/customers?limit=100')
        self.assertEqual(status, 200)
        self.assertEqual(len(payload['items']), 60)
        status, _, payload, _ = self.client.call('GET', '/api/vehicles')
        self.assertEqual(len(payload['items']), 38)   # 四种车队口径共 38 辆
        status, _, payload, _ = self.client.call('GET', '/api/geo/check')
        self.assertEqual(status, 200)

    def test_05_patch_and_delete_reach_router(self):
        """PATCH/DELETE 在 WSGI 下容易被适配层丢掉而返回 405/501。"""
        created = self.client.call('POST', '/api/customers', {
            'code': 'WSGI9', 'product_scope': '姜蒜', 'lon': 104.10, 'lat': 30.70, 'service_min': 10})[2]
        self.assertTrue(created and created.get('item'), created)
        customer_id = created['item']['id']
        status, _, payload, _ = self.client.call('PATCH', f'/api/customers/{customer_id}', {'lat': 30.71})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload['item']['lat'], 30.71)
        status, _, _, _ = self.client.call('DELETE', f'/api/customers/{customer_id}')
        self.assertEqual(status, 200)

    def test_06_plan_run_export_and_monitor(self):
        status, _, payload, _ = self.client.call(
            'POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜', 'solver': 'heuristic'})
        self.assertEqual(status, 200, payload)
        run_id = payload['run_id']
        self.assertEqual(len(payload['trips']), len(payload['schedule']))
        self.assertTrue(payload['charging_plan']['sessions'])
        self.assertIn('dock_plan', payload)

        status, _, payload, _ = self.client.call('GET', f'/api/plan/runs/{run_id}/export?kind=kpi')
        self.assertEqual(status, 200)
        self.assertIn('日总成本(元)', payload['content'])

        status, _, payload, _ = self.client.call('GET', '/api/monitor/state?tick=420')
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload['vehicles'])
        self.assertIn(payload['summary']['worst_status'],
                      ['正常', '临界', '异常', '严重异常'])

    def test_07_logout_clears_session(self):
        status, _, _, _ = self.client.call('POST', '/api/auth/logout')
        self.assertEqual(status, 200)
        status, _, _, _ = self.client.call('GET', '/api/auth/me')
        self.assertEqual(status, 401)

    def test_08_anonymous_cannot_run_plan(self):
        status, _, _, _ = self.client.call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'})
        self.assertEqual(status, 401)


if __name__ == '__main__':
    unittest.main()
