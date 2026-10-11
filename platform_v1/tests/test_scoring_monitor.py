"""五维评分与监控反馈的回归测试。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import api, api_monitor, auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import scoring  # noqa: E402


class MonitorCase(unittest.TestCase):
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
        return api.handle_api(method, target, body, self.settings, {'Cookie': cookie or self.admin})

    def make_run(self, scenario='S3', scope='姜蒜'):
        response = self.call('POST', '/api/plan/run', {'scenario': scenario, 'scope': scope,
                                                       'solver': 'heuristic'})
        self.assertEqual(response.status, 200, response.payload)
        return response.payload


class TestScoring(unittest.TestCase):
    def _kpi(self, cost, on_time, carbon, load, util, distance, penalty=0.0):
        return {'total_cost': cost, 'on_time_rate': on_time, 'carbon_kg': carbon,
                'avg_load_rate': load, 'vehicle_utilization': util,
                'total_distance_km': distance, 'cost_breakdown': {'penalty_cost': penalty}}

    def test_baseline_scores_anchor(self):
        kpi = self._kpi(8000, 90.0, 300.0, 45.0, 100.0, 900.0)
        scored = scoring.score_scenario(kpi, kpi, dock_summary={'conflicts': 0, 'avg_wait_min': 20})
        self.assertEqual(scored['scores']['经济'], scoring.ANCHOR)
        self.assertEqual(scored['scores']['绿色'], scoring.ANCHOR)
        self.assertEqual(scored['scores']['效率'], scoring.ANCHOR)

    def test_better_scenario_scores_higher(self):
        baseline = {'kpi': self._kpi(8000, 80.0, 300.0, 40.0, 100.0, 900.0, 50.0),
                    'dock': {'conflicts': 2, 'avg_wait_min': 30}}
        worse = {'scenario': 'S0', 'kpi': baseline['kpi'], 'dock': baseline['dock']}
        better = {'scenario': 'S3', 'kpi': self._kpi(7000, 96.0, 150.0, 55.0, 100.0, 850.0, 5.0),
                  'dock': {'conflicts': 0, 'avg_wait_min': 12}}
        result = scoring.score_all([worse, better])
        rows = {row['scenario']: row for row in result['items']}
        self.assertGreater(rows['S3']['total_score'], rows['S0']['total_score'])
        self.assertEqual(result['best'], 'S3')
        self.assertEqual(result['ranking'][0], 'S3')
        self.assertGreater(rows['S3']['vs_baseline']['carbon_saving'], 0)
        self.assertEqual(rows['S0']['vs_baseline']['cost_saving'], 0)
        self.assertIn('delta', rows['S3'])
        self.assertEqual(result['dimensions'], ['经济', '时效', '绿色', '协同', '效率'])

    def test_scores_within_range(self):
        items = [{'scenario': s, 'kpi': k, 'dock': {'conflicts': 0, 'avg_wait_min': 15}}
                 for s, k in (('S0', self._kpi(9000, 60.0, 320.0, 30.0, 80.0, 1000.0, 80.0)),
                              ('S1', self._kpi(8000, 90.0, 300.0, 45.0, 100.0, 900.0, 20.0)),
                              ('S2', self._kpi(7200, 95.0, 150.0, 50.0, 100.0, 880.0, 8.0)),
                              ('S3', self._kpi(7000, 98.0, 140.0, 55.0, 100.0, 850.0, 2.0)))]
        result = scoring.score_all(items)
        for row in result['items']:
            for dimension, value in row['scores'].items():
                self.assertGreaterEqual(value, 0.0, f'{row["scenario"]} {dimension} 低于 0')
                self.assertLessEqual(value, 100.0, f'{row["scenario"]} {dimension} 超过 100')
        costs = [row['metrics']['total_cost'] for row in result['items']]
        self.assertEqual(costs, sorted(costs, reverse=True))

    def test_empty_items(self):
        self.assertEqual(scoring.score_all([])['items'], [])


class TestMonitor(MonitorCase):
    def test_no_run_means_404(self):
        self.assertEqual(self.call('GET', '/api/monitor/state').status, 404)

    def test_snapshot_is_deterministic(self):
        run = self.make_run()
        first = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick=420").payload
        second = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick=420").payload
        self.assertEqual(first['vehicles'], second['vehicles'])
        self.assertEqual(first['summary'], second['summary'])

    def test_positions_and_states(self):
        run = self.make_run()
        snap = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick=400").payload
        self.assertEqual(snap['summary']['vehicles'], len(run['trips']))
        self.assertIn(snap['summary']['worst_status'], api_monitor.STATUS_ORDER)
        for vehicle in snap['vehicles']:
            self.assertIn(vehicle['status'], api_monitor.STATUS_ORDER)
            self.assertIn(vehicle['phase'], ('待发车', '配送中', '已完成'))
            self.assertIsNotNone(vehicle['lon'])
            self.assertIsNotNone(vehicle['lat'])
            self.assertLessEqual(vehicle['temperature_limit'], 12.0)
            self.assertTrue(vehicle['energy']['type'] in ('纯电', '燃油'))
            for alert in vehicle['alerts']:
                self.assertIn(alert['level'], (1, 2, 3))
                self.assertTrue(alert['suggestion'])

    def test_before_departure_and_after_completion(self):
        run = self.make_run()
        early = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick=60").payload
        self.assertEqual(early['summary']['waiting'], len(run['trips']))
        self.assertEqual(early['summary']['docks_occupied'], 0)
        # 全部完成的时刻：最后一趟发车 + 该趟时长（求解器给的时序不保证早发）
        durations = {t['trip_id']: float(t['duration_min']) for t in run['trips']}
        last_end = max(int(e['depart']) + durations.get(e['trip_id'], 60) for e in run['schedule'])
        late = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick={int(last_end) + 5}").payload
        self.assertEqual(late['summary']['finished'], len(run['trips']))
        for vehicle in late['vehicles']:
            self.assertGreater(vehicle['travelled_km'], 0)
            self.assertLessEqual(vehicle['travelled_km'], vehicle['distance_km'] + 0.01)

    def test_dock_occupancy_matches_schedule(self):
        run = self.make_run()
        entry = run['schedule'][0]
        tick = int(entry['load_start']) + 1
        snap = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick={tick}").payload
        occupied = [d for d in snap['docks'] if d['occupied']]
        self.assertTrue(occupied)
        self.assertTrue(any(d['trip_id'] == entry['trip_id'] for d in occupied))

    def test_acknowledge_writes_event_and_audit(self):
        run = self.make_run()
        state = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick=400").payload
        alert = None
        for tick in range(360, 560, 20):
            snap = self.call('GET', f"/api/monitor/state?run_id={run['run_id']}&tick={tick}").payload
            if snap['alerts']:
                alert = snap['alerts'][0]
                break
        self.assertIsNotNone(alert, '模拟流中应至少出现一条预警')
        response = self.call('POST', '/api/monitor/events',
                             {'run_id': run['run_id'], 'level': alert['level'],
                              'metric': alert['metric'], 'objects': alert['objects'],
                              'action': '处置', 'note': '已通知司机并记录'})
        self.assertEqual(response.status, 200, response.payload)
        self.assertTrue(response.payload['ok'])
        self.assertGreaterEqual(len(response.payload['events']), 1)
        listed = self.call('GET', '/api/monitor/events').payload['items']
        self.assertEqual(listed[0]['action'], '处置')
        conn = db.connect(self.settings)
        try:
            actions = [row['action'] for row in conn.execute('SELECT action FROM audit_log')]
        finally:
            conn.close()
        self.assertIn('monitor.ack', actions)

    def test_invalid_action_rejected(self):
        run = self.make_run()
        response = self.call('POST', '/api/monitor/events',
                             {'run_id': run['run_id'], 'level': 1, 'metric': '温度', 'action': '瞎写'})
        self.assertEqual(response.status, 400)

    def test_permissions(self):
        self.make_run()
        self.call('POST', '/api/users', {'username': 'view2', 'password': 'abc12345', 'role': 'viewer'})
        viewer = self.login('view2', 'abc12345')
        self.assertEqual(self.call('GET', '/api/monitor/state', cookie=viewer).status, 200)
        denied = self.call('POST', '/api/monitor/events',
                           {'run_id': 1, 'level': 1, 'metric': '温度', 'action': '确认'}, cookie=viewer)
        self.assertEqual(denied.status, 403)
        anonymous = api.handle_api('GET', '/api/monitor/state', b'', self.settings, {})
        self.assertEqual(anonymous.status, 401)


if __name__ == '__main__':
    unittest.main()
