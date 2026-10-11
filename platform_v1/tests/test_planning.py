"""客户→批次→趟次预处理与选题口径成本核算的回归测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import costing, planning  # noqa: E402
from simulation.scheduler import solve_scenario  # noqa: E402


class PlanningCase(unittest.TestCase):
    """每个用例一个独立的临时库，避免用例之间互相污染（如停用某品类客户）。"""

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


class TestBuildPlan(PlanningCase):
    def test_ginger_plan_serves_all_customers(self):
        plan = planning.build_plan(self.conn, '姜蒜', 'baseline')
        self.assertEqual(plan['summary']['customers'], 30)
        self.assertEqual(plan['summary']['served_customers'], 30)
        self.assertGreaterEqual(plan['summary']['trips'], 1)
        served = {code for trip in plan['trips'] for code in trip['customer_codes']}
        self.assertEqual(len(served), 30)

    def test_dual_capacity_constraint(self):
        for scope, fleet in (('姜蒜', 'baseline'), ('鲜面条', 'c3')):
            plan = planning.build_plan(self.conn, scope, fleet)
            for trip in plan['trips']:
                self.assertLessEqual(trip['load_kg'], trip['capacity_kg'] + 1e-6,
                                     f"{trip['trip_id']} 超载")
                self.assertLessEqual(trip['volume_m3'], trip['capacity_m3'] + 1e-6,
                                     f"{trip['trip_id']} 超容积")

    def test_time_window_hard_constraint(self):
        plan = planning.build_plan(self.conn, '姜蒜', 'baseline')
        customers = {c['code']: c for c in plan['customers']}
        for trip in plan['trips']:
            for stop in trip['sequence']:
                acc_end = customers[stop['code']]['acc_end']
                if acc_end is not None:
                    self.assertLessEqual(stop['arrival_min'], acc_end + 1e-6,
                                         f"{stop['code']} 到达越过可接受窗")

    def test_departure_not_before_batch(self):
        plan = planning.build_plan(self.conn, '姜蒜', 'baseline')
        batches = {row['code']: row['depart_after'] for row in
                   self.conn.execute('SELECT code, depart_after FROM batches WHERE product_scope = ?', ('姜蒜',))}
        for trip in plan['trips']:
            self.assertGreaterEqual(trip['planned_departure_min'], batches.get(trip['batch_id'], 0))

    def test_plan_is_deterministic(self):
        first = planning.build_plan(self.conn, '姜蒜', 'baseline')
        second = planning.build_plan(self.conn, '姜蒜', 'baseline')
        self.assertEqual([t['trip_id'] for t in first['trips']], [t['trip_id'] for t in second['trips']])
        self.assertEqual([t['distance_km'] for t in first['trips']], [t['distance_km'] for t in second['trips']])
        self.assertEqual([t['customer_codes'] for t in first['trips']],
                         [t['customer_codes'] for t in second['trips']])

    def test_distance_uses_haversine(self):
        """趟次里程 = Σ 相邻点 Haversine + 返程，且与首站距离可核。"""
        plan = planning.build_plan(self.conn, '鲜面条', 'c3')
        customers = {c['code']: c for c in plan['customers']}
        from simulation.geo import haversine_km, round_km
        for trip in plan['trips']:
            points = [(plan['dc']['lon'], plan['dc']['lat'])]
            for stop in trip['sequence']:
                customer = customers[stop['code']]
                points.append((customer['lon'], customer['lat']))
            points.append((plan['dc']['lon'], plan['dc']['lat']))
            total = sum(haversine_km(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
                        for i in range(len(points) - 1))
            self.assertAlmostEqual(trip['distance_km'], round_km(total), places=1)

    def test_solver_accepts_generated_data(self):
        plan = planning.build_plan(self.conn, '鲜面条', 'c3')
        result = solve_scenario('D3', plan['solver_data'], {'solver': 'heuristic', 'wait_min': 18})
        self.assertGreater(len(result['schedule']), 0)
        self.assertEqual(len(result['schedule']), len(plan['trips']))
        trip_ids = {t['trip_id'] for t in plan['trips']}
        self.assertEqual({row['trip_id'] for row in result['schedule']}, trip_ids)

    def test_c3_fleet_has_charging_windows(self):
        plan = planning.build_plan(self.conn, '姜蒜', 'c3')
        charging = plan['solver_data']['charging']
        ev_codes = {v['code'] for v in plan['vehicles'] if v['energy_type'] == '纯电'}
        self.assertEqual({row['vehicle_id'] for row in charging}, ev_codes)
        for row in charging:
            self.assertGreater(row['charge_end'], row['charge_start'])
            self.assertLessEqual(row['charge_start'], '07:00')     # 谷段充电

    def test_missing_scope_raises(self):
        self.conn.execute("UPDATE customers SET active = 0 WHERE product_scope = '鲜面条'")
        with self.assertRaises(planning.PlanningError):
            planning.build_plan(self.conn, '鲜面条', 'baseline')


class TestCosting(PlanningCase):
    def _priced(self, scope='姜蒜', fleet='baseline'):
        plan = planning.build_plan(self.conn, scope, fleet)
        products = planning.load_products(self.conn)
        params = planning.load_params(self.conn)
        solution = solve_scenario('D3', plan['solver_data'], {'solver': 'heuristic', 'wait_min': 18})
        kpi = costing.price_plan(plan, products, params, solution['schedule'])
        return plan, solution, kpi

    def test_cost_breakdown_sums_to_total(self):
        """各分项之和应等于总成本（差额仅来自逐趟四舍五入，容差 1 元）。"""
        _, _, kpi = self._priced()
        parts = sum(kpi['cost_breakdown'].values())
        self.assertLessEqual(abs(parts - kpi['total_cost']), 1.0,
                             f'分项合计 {parts} 与总成本 {kpi["total_cost"]} 差异过大')
        self.assertGreater(kpi['total_cost'], 0)
        self.assertGreater(kpi['carbon_kg'], 0)
        self.assertGreater(kpi['total_distance_km'], 0)
        self.assertLessEqual(kpi['on_time_rate'], 100.0)
        self.assertGreater(kpi['on_time_rate'], 0.0)
        self.assertLessEqual(kpi['vehicle_utilization'], 100.0)

    def test_electric_trip_has_lower_carbon(self):
        """同里程同服务时长下，纯电的能耗成本与单位里程碳排必须低于燃油。"""
        products = planning.load_products(self.conn)
        params = planning.load_params(self.conn)
        base = {'trip_id': 'X1', 'distance_km': 60.0, 'load_kg': 800, 'volume_m3': 2.0,
                'load_rate': 53.3, 'service_total_min': 60, 'sequence': [], 'customer_codes': []}
        ev_trip = dict(base, vehicle={'code': 'ES1', 'vehicle_type': '小型', 'energy_type': '纯电',
                                      'fixed_cost': 200, 'unit_cost': 2.5, 'maintenance_cost': 40})
        fuel_trip = dict(base, vehicle={'code': 'S1', 'vehicle_type': '小型', 'energy_type': '燃油',
                                        'fixed_cost': 200, 'unit_cost': 2.5, 'maintenance_cost': 40})
        ev = costing.price_trip(ev_trip, {}, products, params)
        fuel = costing.price_trip(fuel_trip, {}, products, params)
        self.assertLess(ev['energy_cost'], fuel['energy_cost'])
        self.assertLess(ev['carbon_kg'] / ev['distance_km'], fuel['carbon_kg'] / fuel['distance_km'])
        self.assertAlmostEqual(ev['transport_cost'], fuel['transport_cost'], places=2)

    def test_range_rule_falls_back_to_fuel(self):
        """第三章新能源可行路线判定：里程超过纯电有效续航时必须改用燃油车。"""
        vehicles = planning.load_vehicles(self.conn, 'c3')
        short = planning._pick_vehicle(500, 2.0, vehicles, 60)
        long = planning._pick_vehicle(500, 2.0, vehicles, 160)   # 纯电有效续航 135 km
        self.assertEqual(short['energy_type'], '纯电')
        self.assertEqual(long['energy_type'], '燃油')

    def test_loss_cost_uses_product_prices(self):
        plan = planning.build_plan(self.conn, '鲜面条', 'baseline')
        products = planning.load_products(self.conn)
        params = planning.load_params(self.conn)
        customers = {c['code']: c for c in plan['customers']}
        detail = costing.price_trip(plan['trips'][0], customers, products, params)
        value = costing.goods_value([customers[code] for code in plan['trips'][0]['customer_codes']], products)
        expected = value * (0.008 + 0.0016)
        self.assertAlmostEqual(detail['loss_cost'], round(expected, 2), places=1)

    def test_service_first_grouping_keeps_on_time_rate_high(self):
        """趟次按时间窗聚类（服务优先）后，准时率必须显著高于"最近邻混编"。"""
        for scope in ('姜蒜', '鲜面条'):
            plan = planning.build_plan(self.conn, scope, 'baseline')
            products = planning.load_products(self.conn)
            params = planning.load_params(self.conn)
            solution = solve_scenario('D3', plan['solver_data'], {'solver': 'heuristic', 'wait_min': 18})
            kpi = costing.price_plan(plan, products, params, solution['schedule'])
            self.assertGreaterEqual(kpi['on_time_rate'], 90.0,
                                    f'{scope} 准时率仅 {kpi["on_time_rate"]}%')
            self.assertLessEqual(kpi['late_hours'], 0.5, f'{scope} 晚到 {kpi["late_hours"]} 小时')

    def test_compare_scenarios_marginal_effect(self):
        results = []
        for scenario, solver_scenario in (('S0', 'H0'), ('S1', 'S1'), ('S2', 'C3'), ('S3', 'D3')):
            plan = planning.build_plan(self.conn, '姜蒜', 'baseline' if scenario in ('S0', 'S1') else 'c3')
            products = planning.load_products(self.conn)
            params = planning.load_params(self.conn)
            solution = solve_scenario(solver_scenario, plan['solver_data'],
                                      {'solver': 'heuristic', 'wait_min': 18})
            kpi = costing.price_plan(plan, products, params, solution['schedule'])
            results.append({'scenario': scenario, 'kpi': kpi})
        comparison = costing.compare_scenarios(results)
        self.assertEqual([row['scenario'] for row in comparison['items']], ['S0', 'S1', 'S2', 'S3'])
        self.assertIn('vs_baseline', comparison['items'][3])
        self.assertNotIn('delta', comparison['items'][0])
        self.assertIn('delta', comparison['items'][1])


if __name__ == '__main__':
    unittest.main()
