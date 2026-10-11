"""真实 HTTP 服务的端到端测试（含静态资源、错误码、无目录列表）。

用随机端口在本进程内启动 ``server.PlatformHTTPServer``，再用 http.client
发真实请求，验证部署后 Nginx 反代到的那一层确实可用。
"""
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from app import api  # noqa: E402
from app.config import build_settings  # noqa: E402


class TestHttpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from app import auth as auth_module

        api.clear_caches()
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_ACCESS_LOG': '0',
            'PLATFORM_DB': str(Path(cls.tmpdir.name) / 'platform.db'),
        })
        auth_module.bootstrap(cls.settings)
        server.PlatformHandler.settings = cls.settings
        cls.httpd = server.PlatformHTTPServer(('127.0.0.1', 0), server.PlatformHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmpdir.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            conn.close()

    def test_index_page(self):
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('仓配一体化协同平台', body.decode('utf-8'))

    def test_static_asset_and_304(self):
        status, headers, body = self.request('GET', '/app.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertEqual(str(len(body)), headers['Content-Length'])
        status2, _, _ = self.request('GET', '/app.js', headers={'If-None-Match': headers['ETag']})
        self.assertEqual(status2, 304)

    def test_head_request(self):
        status, headers, body = self.request('HEAD', '/styles.css')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertGreater(int(headers['Content-Length']), 0)

    def test_health(self):
        status, headers, body = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        payload = json.loads(body.decode('utf-8'))
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_solve_post(self):
        body = json.dumps({'scenario': 'D3', 'params': {'solver': 'heuristic'}}).encode('utf-8')
        status, headers, raw = self.request(
            'POST', '/api/solve', body=body, headers={'Content-Type': 'application/json'}
        )
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode('utf-8'))
        self.assertTrue(payload['schedule'])

    def test_api_error_is_json(self):
        status, headers, raw = self.request(
            'POST', '/api/solve', body=b'{broken', headers={'Content-Type': 'application/json'}
        )
        self.assertEqual(status, 400)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertIn('error', json.loads(raw.decode('utf-8')))

    def test_empty_json_object_uses_defaults(self):
        status, headers, raw = self.request(
            'POST', '/api/solve', body=b'{}', headers={'Content-Type': 'application/json'}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw.decode('utf-8'))['scenario'], 'D3')

    def test_unknown_api_json_404(self):
        status, headers, raw = self.request('GET', '/api/whatever')
        self.assertEqual(status, 404)
        self.assertIn('error', json.loads(raw.decode('utf-8')))

    def test_missing_static_404_html(self):
        status, headers, raw = self.request('GET', '/no-such-file.txt')
        self.assertEqual(status, 404)
        self.assertIn('text/html', headers['Content-Type'])

    def test_no_directory_listing(self):
        status, _, _ = self.request('GET', '/')
        self.assertEqual(status, 200)
        status, _, raw = self.request('GET', '/data/')
        self.assertEqual(status, 404)

    def test_traversal_blocked(self):
        status, _, _ = self.request('GET', '/../server.py')
        self.assertEqual(status, 404)

    def test_patch_and_delete_are_routed(self):
        """BaseHTTPRequestHandler 默认不支持 PATCH/DELETE，必须显式实现，
        否则管理接口的编辑/删除会返回 501。这里做端到端回归。"""
        login_body = json.dumps({'username': 'admin', 'password': 'admin123'}).encode('utf-8')
        status, headers, raw = self.request('POST', '/api/auth/login', body=login_body,
                                            headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 200, raw[:200])
        cookie = headers['Set-Cookie'].split(';')[0]

        create_body = json.dumps({'code': '950', 'product_scope': '姜蒜', 'lon': 104.10,
                                  'lat': 30.70, 'service_min': 10}).encode('utf-8')
        status, _, raw = self.request('POST', '/api/customers', body=create_body,
                                      headers={'Content-Type': 'application/json', 'Cookie': cookie})
        self.assertEqual(status, 200, raw[:300])
        customer_id = json.loads(raw.decode('utf-8'))['item']['id']

        patch_body = json.dumps({'lat': 30.71}).encode('utf-8')
        status, _, raw = self.request('PATCH', f'/api/customers/{customer_id}', body=patch_body,
                                      headers={'Content-Type': 'application/json', 'Cookie': cookie})
        self.assertEqual(status, 200, f'PATCH 应返回 200，实际 {status}: {raw[:200]}')
        self.assertEqual(json.loads(raw.decode('utf-8'))['item']['lat'], 30.71)

        status, _, raw = self.request('DELETE', f'/api/customers/{customer_id}', headers={'Cookie': cookie})
        self.assertEqual(status, 200, f'DELETE 应返回 200，实际 {status}: {raw[:200]}')


if __name__ == '__main__':
    unittest.main()
