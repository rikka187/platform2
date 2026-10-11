"""论文口径求解器（ALNS 移植）与第三章车队口径的回归测试。

这些用例锁定了本轮修掉的几个真实缺陷：
* 单客户趟次曾被判为不可行（`len(route) < 4`）→ 真实时间窗下直接无解；
* ALNS 路径曾被叠加 H0 的 1.22 人工绕行系数；
* 车队对比曾各跑一次 ALNS，随机性导致"8 纯电反而比 6 纯电碳排高"；
* 车队车辆查询缺少载重/容积字段，导致"运力不足"误报。
"""
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
from simulation import joint_plan, lifecycle, vrp_optimizer as vrp  # noqa: E402


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_DB': str(Path(self.tmp.name) / 'platform.db'),
        })
        auth.bootstrap(self.settings)
        self.conn = db.connect(self.settings)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestVrpOptimizer(BaseCase):
    def test_problem_builds_both_profiles(self):
        real = vrp.build_problem(self.conn, 'baseline', profile='case2')
        self.assertEqual(len(real.nodes['姜蒜']), 30)
        self.assertEqual(len(real.nodes['鲜面条']), 30)
        paper = vrp.build_problem(self.conn, 'baseline', profile='paper')
        self.assertEqual(len(paper.nodes['姜蒜']), 30)
        # 论文构造算例的批次合计应与团队脚本一致（姜蒜 7110 / 1721）
        batch_kg = {}
        for node in paper.nodes['姜蒜']:
            batch_kg[node.batch] = batch_kg.get(node.batch, 0) + node.demand
        self.assertAlmostEqual(batch_kg['批次一'], 7110, places=0)
        self.assertAlmostEqual(batch_kg['批次二'], 1721, places=0)
        # 两种口径的车辆池都来自平台车辆表
        self.assertEqual(paper.vehicles['小型']['capacity'], 1500.0)
        self.assertEqual(paper.vehicles['小型']['fixed'], 200.0)

    def test_single_customer_route_is_feasible(self):
        """[0, 客户, 0] 是合法趟次（原程序 len<4 会判死，导致单点兜底不可达）。"""
        problem = vrp.build_problem(self.conn, 'baseline', profile='paper')
        node = problem.nodes['姜蒜'][0]
        ok, info = vrp.evaluate_route(problem, [0, ('姜蒜', node.index), 0])
        self.assertTrue(ok, '单客户趟次应可行')
        self.assertEqual(info['load'], node.demand)
        self.assertGreater(info['route_cost'], 0)

    def test_product_and_batch_cannot_mix(self):
        problem = vrp.build_problem(self.conn, 'baseline', profile='paper')
        gg = problem.nodes['姜蒜']
        nm = problem.nodes['鲜面条']
        same = [0, ('姜蒜', gg[0].index), ('姜蒜', gg[1].index), 0]
        ok, _ = vrp.evaluate_route(problem, same)
        self.assertEqual(ok, gg[0].batch == gg[1].batch, '同批次才能合并')
        mixed_product = [0, ('姜蒜', gg[0].index), ('鲜面条', nm[0].index), 0]
        self.assertFalse(vrp.evaluate_route(problem, mixed_product)[0], '品类禁混')
        batch_a = next(n for n in gg if n.batch == '批次一')
        batch_b = next(n for n in gg if n.batch == '批次二')
        mixed_batch = [0, ('姜蒜', batch_a.index), ('姜蒜', batch_b.index), 0]
        self.assertFalse(vrp.evaluate_route(problem, mixed_batch)[0], '批次禁混')

    def test_capacity_and_volume_are_enforced(self):
        problem = vrp.build_problem(self.conn, 'baseline', profile='case2')
        nodes = sorted(problem.nodes['姜蒜'], key=lambda n: -n.demand)
        heavy = nodes[0]
        # 需求最大的客户单独一趟必须可行，且必须选中型车
        ok, info = vrp.evaluate_route(problem, [0, ('姜蒜', heavy.index), 0])
        self.assertTrue(ok)
        self.assertEqual(info['vehicle'], '中型' if heavy.demand > 1500 else '小型')

    def test_departure_policy_switch(self):
        problem = vrp.build_problem(self.conn, 'baseline', profile='paper')
        nodes = [n for n in problem.nodes['姜蒜'] if n.batch == '批次一'][:2]
        route = [0] + [('姜蒜', n.index) for n in nodes] + [0]
        vrp.DEPARTURE_POLICY = 'ready'
        try:
            ok, info = vrp.evaluate_route(problem, route)
            self.assertTrue(ok)
            self.assertAlmostEqual(info['depart'], problem.ready_time('姜蒜', '批次一'), places=3)
        finally:
            vrp.DEPARTURE_POLICY = 'ready'

    def test_paper_profile_can_solve_small_run(self):
        """小规模跑通求解链路（正式参数为 5×3000，这里只验证不抛错且结构完整）。"""
        problem = vrp.build_problem(self.conn, 'baseline', profile='paper')
        result = vrp.solve(problem, restarts=1, iterations=120)
        summary = result['summary']
        self.assertGreaterEqual(summary['trips'], 1)
        self.assertEqual(summary['customers'], 60)
        self.assertGreater(summary['delivery_cost'], 0)
        vehicles = {trip['vehicle_code'] for trip in result['trips']}
        self.assertLessEqual(len(vehicles), 10, '用车不得超过车辆池')


class TestLifecycle(BaseCase):
    def _vehicles(self, fleet):
        return [dict(row) for row in self.conn.execute(
            'SELECT code, vehicle_type, energy_type, capacity_kg, capacity_m3, purchase_price_wan, '
            '       maintenance_cost FROM vehicles WHERE fleet_tag = ? AND available = 1 '
            "ORDER BY vehicle_type, CASE energy_type WHEN '纯电' THEN 0 ELSE 1 END, code", (fleet,))]

    def _trips(self):
        # 早高峰 7 趟并发（超过 C3 的 6 辆纯电 → 必须动用燃油车；C4 的 8 辆纯电则够用），
        # 另有 1 趟晚些发车可由返场车辆复用
        trips = []
        for index in range(1, 8):
            trips.append({'trip_id': f'T{index}', 'load_kg': 1000 - index * 50,
                          'volume_m3': 5.0, 'distance_km': 60.0, 'duration_min': 120,
                          'drive_min': 90, 'service_total_min': 30,
                          'planned_departure_min': 270 + index})
        trips.append({'trip_id': 'T8', 'load_kg': 300, 'volume_m3': 2.0, 'distance_km': 40.0,
                      'duration_min': 90, 'drive_min': 60, 'service_total_min': 20,
                      'planned_departure_min': 420})
        return trips

    def test_assign_routes_prefers_electric(self):
        trips = self._trips()
        vehicles = self._vehicles('c3')
        assignment = lifecycle.assign_routes(trips, vehicles)
        self.assertIsNotNone(assignment)
        self.assertEqual(len(assignment), len(trips))
        first = assignment['T1']
        self.assertEqual(first['energy_type'], '纯电', '应优先派纯电车')

    def test_assign_routes_respects_capacity(self):
        heavy = [{'trip_id': 'H1', 'load_kg': 6000, 'volume_m3': 30.0, 'distance_km': 50.0,
                  'duration_min': 100, 'planned_departure_min': 300}]
        assignment = lifecycle.assign_routes(heavy, self._vehicles('c3'))
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment['H1']['vehicle_type'], '中型')

    def test_ev_fleet_has_lower_carbon_on_same_trips(self):
        """同一组任务下，纯电比例更高的车队碳排必须更低（本轮修掉的排序 bug）。"""
        trips = self._trips()
        params = {row['key']: row['value'] for row in self.conn.execute('SELECT key, value FROM params')}
        params.update({k: v for k, v in lifecycle.DEFAULTS.items() if k not in params})
        c0 = lifecycle.evaluate_fleet_on_routes(trips, self._vehicles('baseline'), params)
        c3 = lifecycle.evaluate_fleet_on_routes(trips, self._vehicles('c3'), params)
        c4 = lifecycle.evaluate_fleet_on_routes(trips, self._vehicles('c4'), params)
        for result in (c0, c3, c4):
            self.assertIsNotNone(result)
        self.assertLess(c3['carbon_kg'], c0['carbon_kg'])
        self.assertLess(c4['carbon_kg'], c3['carbon_kg'])
        self.assertEqual(min([c0, c3, c4], key=lambda r: r['carbon_kg'])['carbon_kg'], c4['carbon_kg'])

    def test_vehicle_cost_model(self):
        params = {row['key']: row['value'] for row in self.conn.execute('SELECT key, value FROM params')}
        params.update({k: v for k, v in lifecycle.DEFAULTS.items() if k not in params})
        ev = {'code': 'E1', 'vehicle_type': '小型', 'energy_type': '纯电', 'purchase_price_wan': 18.0}
        fuel = {'code': 'F1', 'vehicle_type': '小型', 'energy_type': '燃油', 'purchase_price_wan': 12.0}
        ev_cost = lifecycle.vehicle_daily_cost(ev, 60, 1.5, 0.5, params)
        fuel_cost = lifecycle.vehicle_daily_cost(fuel, 60, 1.5, 0.5, params)
        self.assertLess(ev_cost['energy_cost'], fuel_cost['energy_cost'])
        self.assertLess(ev_cost['carbon_kg'], fuel_cost['carbon_kg'])
        self.assertGreater(ev_cost['amortization'], fuel_cost['amortization'])


class TestFleetEvalApi(BaseCase):
    def setUp(self):
        super().setUp()
        login = api.handle_api('POST', '/api/auth/login',
                               json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
                               self.settings, {})
        self.cookie = 'platform_session=' + re.search(
            r'platform_session=([^;]+)', login.headers['Set-Cookie']).group(1)

    def call(self, target, payload):
        return api.handle_api('POST', target, json.dumps(payload).encode(), self.settings,
                              {'Cookie': self.cookie})

    def test_unknown_scenario_rejected(self):
        response = self.call('/api/plan/fleet-eval', {'scenarios': ['C9']})
        self.assertEqual(response.status, 400)

    def test_requires_scenario_permission(self):
        anonymous = api.handle_api('POST', '/api/plan/fleet-eval',
                                   json.dumps({'scenarios': ['C0']}).encode(), self.settings, {})
        self.assertEqual(anonymous.status, 401)

    def test_comparison_ranks_lowest_carbon_by_ev_share(self):
        response = self.call('/api/plan/fleet-eval',
                             {'profile': 'paper', 'restarts': 1, 'iterations': 120})
        self.assertEqual(response.status, 200, response.payload)
        data = response.payload
        by_name = {item['scenario']: item for item in data['items']}
        self.assertEqual(set(by_name), {'C0', 'C3', 'C4'})
        self.assertLess(by_name['C3']['carbon_kg'], by_name['C0']['carbon_kg'])
        self.assertLess(by_name['C4']['carbon_kg'], by_name['C3']['carbon_kg'])
        self.assertEqual(data['lowest_carbon'], 'C4')
        # 同一组任务的里程应完全一致（对比的是配置，不是重新规划）
        distances = {round(item['distance_km'], 2) for item in data['items']}
        self.assertEqual(len(distances), 1, f'各配置里程应一致，实际 {distances}')
        self.assertEqual(by_name['C0']['used_vehicles'], by_name['C0']['used_vehicles'])
        # 成本明细四项齐全
        for item in data['items']:
            for key in ('amortization', 'energy_cost', 'maintenance', 'fixed_annual'):
                self.assertIn(key, item['breakdown'])


if __name__ == '__main__':
    unittest.main()
