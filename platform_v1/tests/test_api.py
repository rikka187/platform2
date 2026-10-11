"""接口层与静态资源解析的回归测试。

运行：``python -m unittest discover -s tests -v``
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import api  # noqa: E402
from app.config import build_settings  # noqa: E402


def make_settings(**overrides):
    env = {
        'PLATFORM_ROOT': str(ROOT),
        'PLATFORM_PORT': '8787',
    }
    env.update(overrides)
    return build_settings(env)


def call(method, target, body=b'', settings=None):
    """兼容旧断言的调用助手：返回 (status, payload)。"""
    response = api.handle_api(method, target, body, settings)
    return response.status, response.payload


class TestApiRoutes(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings()
        api.clear_caches()

    def test_health(self):
        status, payload = call('GET', '/api/health', b'', self.settings)
        self.assertEqual(status, 200)
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['data']['ready'])
        self.assertGreater(payload['data']['counts']['trips'], 0)

    def test_demo(self):
        status, payload = call('GET', '/api/demo', b'', self.settings)
        self.assertEqual(status, 200)
        for key in ('orders', 'trips', 'vehicles', 'docks', 'prep', 'charging'):
            self.assertTrue(payload[key], f'{key} 不应为空')

    def test_scenarios(self):
        status, payload = call('GET', '/api/scenarios', b'', self.settings)
        self.assertEqual(status, 200)
        self.assertEqual(payload['scenarios'], ['H0', 'S0', 'S1', 'C3', 'D3'])
        self.assertEqual(payload['defaults']['D3'], 18)

    def test_solver_status_contract(self):
        status, payload = call('GET', '/api/solver/status', b'', self.settings)
        self.assertEqual(status, 200)
        self.assertIn('heuristic', payload['available'])
        self.assertIn(payload['default_engine'], {'auto', 'heuristic', 'cpsat'})

    def test_config_reports_default_engine(self):
        settings = make_settings(PLATFORM_DEFAULT_SOLVER='heuristic')
        status, payload = call('GET', '/api/config', b'', settings)
        self.assertEqual(status, 200)
        self.assertEqual(payload['default_solver'], 'heuristic')

    def test_solve_ok(self):
        body = json.dumps({'scenario': 'D3', 'params': {'wait_min': 18, 'solver': 'heuristic'}}).encode('utf-8')
        status, payload = call('POST', '/api/solve', body, self.settings)
        self.assertEqual(status, 200)
        self.assertEqual(payload['scenario'], 'D3')
        self.assertTrue(payload['schedule'])
        self.assertIn('total_cost', payload['kpi'])

    def test_solve_is_cached(self):
        body = json.dumps({'scenario': 'D3', 'params': {'solver': 'heuristic', 'wait_min': 18}}).encode('utf-8')
        call('POST', '/api/solve', body, self.settings)
        _, payload = call('POST', '/api/solve', body, self.settings)
        self.assertTrue(payload.get('cached'))

    def test_solve_unknown_scenario(self):
        body = json.dumps({'scenario': 'X9', 'params': {}}).encode('utf-8')
        status, payload = call('POST', '/api/solve', body, self.settings)
        self.assertEqual(status, 400)
        self.assertIn('未知情景', payload['error'])

    def test_solve_bad_solver(self):
        body = json.dumps({'scenario': 'D3', 'params': {'solver': 'gurobi'}}).encode('utf-8')
        status, payload = call('POST', '/api/solve', body, self.settings)
        self.assertEqual(status, 400)
        self.assertIn('求解器', payload['error'])

    def test_solve_bad_json(self):
        status, payload = call('POST', '/api/solve', b'{not json', self.settings)
        self.assertEqual(status, 400)
        self.assertIn('JSON', payload['error'])

    def test_solve_empty_body(self):
        status, payload = call('POST', '/api/solve', b'', self.settings)
        self.assertEqual(status, 400)
        self.assertIn('请求体为空', payload['error'])

    def test_solve_body_too_large(self):
        settings = make_settings(PLATFORM_MAX_BODY_BYTES='1024')
        body = b'{"scenario":"D3","params":{}}' + b' ' * 2048
        status, payload = call('POST', '/api/solve', body, settings)
        self.assertEqual(status, 413)

    def test_params_are_clamped_and_filtered(self):
        params = api.normalize_params({
            'solver': 'CP-SAT',
            'wait_min': 99999,
            'max_time_seconds': 9999,
            'num_search_workers': 999,
            'turnover_min': -5,
            'evil': 'rm -rf /',
        }, self.settings)
        self.assertEqual(params['solver'], 'cpsat')
        self.assertEqual(params['wait_min'], 1440)
        self.assertLessEqual(params['max_time_seconds'], self.settings.cpsat_max_time)
        self.assertLessEqual(params['num_search_workers'], self.settings.worker_count)
        self.assertEqual(params['turnover_min'], 0)
        self.assertNotIn('evil', params)

    def test_compare_heuristic(self):
        status, payload = call('GET', '/api/compare?solver=heuristic', b'', self.settings)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload['items']), 5)
        costs = {item['scenario']: item['kpi']['total_cost'] for item in payload['items']}
        self.assertGreater(costs['H0'], costs['S0'])
        self.assertGreater(costs['S0'], costs['D3'])

    def test_unknown_api(self):
        status, payload = call('GET', '/api/nope', b'', self.settings)
        self.assertEqual(status, 404)
        self.assertIn('未知接口', payload['error'])

    def test_method_not_allowed(self):
        status, payload = call('DELETE', '/api/demo', b'', self.settings)
        self.assertEqual(status, 405)

    def test_missing_data_dir_gives_readable_error(self):
        settings = make_settings(PLATFORM_DATA_DIR=str(ROOT / 'data' / 'not-exists'))
        status, payload = call('GET', '/api/demo', b'', settings)
        self.assertEqual(status, 500)
        self.assertIn('数据目录不存在', payload['error'])


class TestStaticResolution(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings()

    def test_index(self):
        target = api.resolve_static('/', self.settings)
        self.assertIsNotNone(target)
        self.assertEqual(target.name, 'index.html')

    def test_asset(self):
        target = api.resolve_static('/app.js?v=1', self.settings)
        self.assertIsNotNone(target)
        self.assertEqual(target.name, 'app.js')
        self.assertIn('javascript', api.content_type_for(target))

    def test_traversal_blocked(self):
        for path in ('/../server.py', '/..%2fserver.py', '/%2e%2e/server.py', '/sub/../../server.py', '/..\\server.py'):
            self.assertIsNone(api.resolve_static(path, self.settings), path)

    def test_directory_not_listed(self):
        self.assertIsNone(api.resolve_static('/', make_settings(PLATFORM_STATIC_DIR=str(ROOT))))

    def test_missing_file(self):
        self.assertIsNone(api.resolve_static('/nope.txt', self.settings))

    def test_index_is_not_cached(self):
        target = api.resolve_static('/', self.settings)
        self.assertIn('no-store', api.cache_control_for(target))
        asset = api.resolve_static('/styles.css', self.settings)
        self.assertIn('max-age', api.cache_control_for(asset))


class TestDataLoading(unittest.TestCase):
    def test_gbk_csv_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = ROOT / 'data' / 'demo'
            dst = Path(tmp) / 'demo'
            shutil.copytree(src, dst)
            (dst / 'vehicles.csv').write_bytes((src / 'vehicles.csv').read_text(encoding='utf-8').encode('gb18030'))
            settings = make_settings(PLATFORM_DATA_DIR=str(dst))
            api.clear_caches()
            status, payload = call('GET', '/api/demo', b'', settings)
            self.assertEqual(status, 200)
            self.assertEqual(len(payload['vehicles']), 10)

    def test_missing_column_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = ROOT / 'data' / 'demo'
            dst = Path(tmp) / 'demo'
            shutil.copytree(src, dst)
            (dst / 'trips.csv').write_text('trip_id,batch_id\nR1,B1\n', encoding='utf-8')
            settings = make_settings(PLATFORM_DATA_DIR=str(dst))
            api.clear_caches()
            status, payload = call('GET', '/api/demo', b'', settings)
            self.assertEqual(status, 500)
            self.assertIn('缺少必需列', payload['error'])

    def test_health_degrades_when_data_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(PLATFORM_DATA_DIR=str(Path(tmp) / 'empty'))
            status, payload = call('GET', '/api/health', b'', settings)
            self.assertEqual(status, 200)
            self.assertEqual(payload['status'], 'degraded')


if __name__ == '__main__':
    unittest.main()
