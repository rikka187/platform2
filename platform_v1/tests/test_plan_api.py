"""方案接口回归测试：S0-S3 情景、落库回放、权限与对比。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import api, auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402


class PlanApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_DB': str(Path(self.tmp.name) / 'platform.db'),
        })
        auth.bootstrap(self.settings)
        self.admin = self.login('admin', 'admin123')

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, password):
        response = api.handle_api('POST', '/api/auth/login',
                                  json.dumps({'username': username, 'password': password}).encode(),
                                  self.settings, {})
        self.assertEqual(response.status, 200, response.payload)
        return 'platform_session=' + re.search(r'platform_session=([^;]+)',
                                               response.headers['Set-Cookie']).group(1)

    def call(self, method, target, payload=None, cookie=None):
        body = json.dumps(payload).encode() if payload is not None else b''
        return api.handle_api(method, target, body, self.settings,
                              {'Cookie': cookie or self.admin})


class TestPlanRun(PlanApiCase):
    def test_run_s3_returns_full_plan(self):
        response = self.call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'})
        self.assertEqual(response.status, 200, response.payload)
        data = response.payload
        self.assertIsInstance(data['run_id'], int)
        self.assertEqual(data['scenario'], 'S3')
        self.assertEqual(data['fleet_tag'], 'c3')
        self.assertEqual(data['solver_scenario'], 'D3')
        self.assertEqual(len(data['customers']), 30)
        self.assertGreater(len(data['trips']), 0)
        self.assertEqual(len(data['schedule']), len(data['trips']))
        kpi = data['kpi']
        for key in ('total_cost', 'on_time_rate', 'carbon_kg', 'vehicle_utilization',
                    'avg_load_rate', 'total_distance_km', 'cost_breakdown'):
            self.assertIn(key, kpi)
        self.assertGreaterEqual(kpi['on_time_rate'], 90.0)
        self.assertGreater(len(data['charging_plan']['sessions']), 0)   # C3 车队有充电排班
        self.assertIn('dock_plan', data)
        self.assertEqual(data['dock_plan']['summary']['trips'], len(data['trips']))

    def test_scenario_fleet_mapping(self):
        baseline = self.call('POST', '/api/plan/run', {'scenario': 'S0', 'scope': '姜蒜'}).payload
        new_energy = self.call('POST', '/api/plan/run', {'scenario': 'S2', 'scope': '姜蒜'}).payload
        self.assertEqual(baseline['fleet_tag'], 'baseline')
        self.assertEqual(baseline['solver_scenario'], 'H0')
        self.assertEqual(new_energy['fleet_tag'], 'c3')
        self.assertEqual(new_energy['solver_scenario'], 'C3')
        self.assertLess(new_energy['kpi']['carbon_kg'], baseline['kpi']['carbon_kg'])

    def test_run_persists_and_replays(self):
        created = self.call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '鲜面条'}).payload
        run_id = created['run_id']
        detail = self.call('GET', f'/api/plan/runs/{run_id}')
        self.assertEqual(detail.status, 200)
        self.assertEqual(detail.payload['run_id'], run_id)
        self.assertEqual(len(detail.payload['trips']), len(created['trips']))
        self.assertEqual(detail.payload['kpi']['total_cost'], created['kpi']['total_cost'])
        latest = self.call('GET', '/api/plan/latest')
        self.assertEqual(latest.payload['run_id'], run_id)
        listing = self.call('GET', '/api/plan/runs?limit=5')
        self.assertEqual(listing.payload['items'][0]['id'], run_id)
        self.assertIn('S0', listing.payload['scenarios'])

    def test_invalid_scenario_and_scope(self):
        self.assertEqual(self.call('POST', '/api/plan/run', {'scenario': 'X9'}).status, 400)
        self.assertEqual(self.call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '肉类'}).status, 400)
        unknown_fleet = self.call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '姜蒜',
                                                           'fleet': 'nope'})
        self.assertEqual(unknown_fleet.status, 400)
        self.assertIn('未知车队', unknown_fleet.payload['error'])
        ok_fleet = self.call('POST', '/api/plan/run', {'scenario': 'S2', 'scope': '姜蒜',
                                                      'fleet': 'c3_10', 'solver': 'heuristic'})
        self.assertEqual(ok_fleet.status, 200)
        self.assertEqual(ok_fleet.payload['fleet_tag'], 'c3_10')

    def test_export_trip_and_kpi_csv(self):
        created = self.call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜',
                                                      'solver': 'heuristic'}).payload
        run_id = created['run_id']
        trips = self.call('GET', '/api/plan/runs/' + str(run_id) + '/export?kind=trip').payload
        self.assertEqual(trips['filename'], 'plan' + str(run_id) + '_trips.csv')
        self.assertTrue(trips['content'].startswith('\ufeff'), 'CSV 应带 BOM 以便 Excel 识别中文')
        lines = [line for line in trips['content'].splitlines() if line.strip()]
        self.assertEqual(len(lines), len(created['trips']) + 1)      # 表头 + 每趟一行
        self.assertIn('合计成本', lines[0])
        self.assertIn('发车时刻', lines[0])
        self.assertIn(created['trips'][0]['trip_id'], lines[1])

        kpi = self.call('GET', '/api/plan/runs/' + str(run_id) + '/export?kind=kpi').payload
        self.assertEqual(kpi['filename'], 'plan' + str(run_id) + '_kpi.csv')
        self.assertIn('日总成本(元)', kpi['content'])
        self.assertIn('准时率(%)', kpi['content'])
        self.assertIn('碳排放(kg)', kpi['content'])
        self.assertIn(str(created['kpi']['total_cost']), kpi['content'])

    def test_export_rejects_bad_kind_and_missing_run(self):
        run = self.call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '姜蒜',
                                                  'solver': 'heuristic'}).payload
        bad = self.call('GET', '/api/plan/runs/' + str(run['run_id']) + '/export?kind=pdf')
        self.assertEqual(bad.status, 400)
        missing = self.call('GET', '/api/plan/runs/999/export?kind=trip')
        self.assertEqual(missing.status, 404)

    def test_export_requires_login(self):
        run = self.call('POST', '/api/plan/run', {'scenario': 'S1', 'scope': '姜蒜',
                                                  'solver': 'heuristic'}).payload
        anonymous = api.handle_api('GET', '/api/plan/runs/' + str(run['run_id']) + '/export?kind=trip',
                                   b'', self.settings, {})
        self.assertEqual(anonymous.status, 401)

    def test_compare_scenarios(self):
        response = self.call('POST', '/api/plan/compare', {'scope': '姜蒜'})
        self.assertEqual(response.status, 200, response.payload)
        data = response.payload
        self.assertEqual(len(data['items']), 4)
        self.assertEqual([row['scenario'] for row in data['comparison']['items']],
                         ['S0', 'S1', 'S2', 'S3'])
        self.assertIn('vs_baseline', data['comparison']['items'][3])
        # 碳排应随新能源替换而下降
        s0_carbon = data['items'][0]['kpi']['carbon_kg']
        s3_carbon = data['items'][3]['kpi']['carbon_kg']
        self.assertLess(s3_carbon, s0_carbon)

    def test_viewer_cannot_run(self):
        self.call('POST', '/api/users', {'username': 'view1', 'password': 'abc12345', 'role': 'viewer'})
        viewer = self.login('view1', 'abc12345')
        denied = self.call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'}, cookie=viewer)
        self.assertEqual(denied.status, 403)
        allowed = self.call('GET', '/api/plan/runs', cookie=viewer)
        self.assertEqual(allowed.status, 200)

    def test_requires_login(self):
        anonymous = api.handle_api('POST', '/api/plan/run', b'{}', self.settings, {})
        self.assertEqual(anonymous.status, 401)

    def test_writes_audit_and_tables(self):
        created = self.call('POST', '/api/plan/run', {'scenario': 'S3', 'scope': '姜蒜'}).payload
        conn = db.connect(self.settings)
        try:
            counts = db.table_counts(conn)
            self.assertEqual(counts['runs'], 1)
            self.assertEqual(counts['trips'], len(created['trips']))
            actions = [row['action'] for row in conn.execute('SELECT action FROM audit_log')]
            self.assertIn('plan.run', actions)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
