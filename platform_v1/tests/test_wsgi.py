"""WSGI 入口测试（宝塔 Python 项目 / gunicorn 部署方式）。"""
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wsgi  # noqa: E402
from app import api  # noqa: E402


class Response:
    def __init__(self):
        self.status = None
        self.headers = []
        self.body = b''

    @property
    def payload(self):
        return json.loads(self.body.decode('utf-8'))

    def header(self, name):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


def call(method='GET', path='/', query='', body=b''):
    api.clear_caches()
    environ = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'QUERY_STRING': query,
        'CONTENT_LENGTH': str(len(body)) if body else '',
        'CONTENT_TYPE': 'application/json',
        'wsgi.input': io.BytesIO(body),
    }
    response = Response()

    def start_response(status, headers, exc_info=None):
        response.status = int(status.split(' ', 1)[0])
        response.headers = headers

    chunks = wsgi.application(environ, start_response)
    response.body = b''.join(chunks)
    return response


class TestWsgi(unittest.TestCase):
    def test_index(self):
        response = call('GET', '/')
        self.assertEqual(response.status, 200)
        self.assertIn('text/html', response.header('Content-Type'))
        self.assertIn('仓配一体化协同平台', response.body.decode('utf-8'))

    def test_health(self):
        response = call('GET', '/api/health')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload['status'], 'ok')

    def test_solve(self):
        body = json.dumps({'scenario': 'D3', 'params': {'solver': 'heuristic'}}).encode('utf-8')
        response = call('POST', '/api/solve', body=body)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.payload['schedule'])

    def test_body_too_large(self):
        response = call('POST', '/api/solve', body=b'x' * (200 * 1024))
        self.assertEqual(response.status, 413)

    def test_static_asset(self):
        response = call('GET', '/styles.css')
        self.assertEqual(response.status, 200)
        self.assertIn('text/css', response.header('Content-Type'))

    def test_traversal(self):
        response = call('GET', '/../server.py')
        self.assertEqual(response.status, 404)

    def test_unknown_api(self):
        response = call('GET', '/api/none')
        self.assertEqual(response.status, 404)

    def test_method_not_allowed_on_static(self):
        response = call('POST', '/styles.css')
        self.assertEqual(response.status, 405)


if __name__ == '__main__':
    unittest.main()
