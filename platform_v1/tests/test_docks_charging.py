"""月台协同与充电排班的回归测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import charging, docks, planning, costing  # noqa: E402
from simulation.scheduler import solve_scenario  # noqa: E402


class DocksAndChargingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_DB': str(Path(self.tmp.name) / 'platform.db'),
        })
        auth.bootstrap(self.settings)
        self.conn = db.connect(self.settings)
        self.params = planning.load_params(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _solve(self, scope='姜蒜', fleet='c3', solver='heuristic'):
        plan = planning.build_plan(self.conn, scope, fleet)
        solution = solve_scenario('D3', plan['solver_data'], {'solver': solver, 'wait_min': 18})
        return plan, solution


class TestDockCoordination(DocksAndChargingCase):
    def test_prep_backward_and_timeline(self):
        plan, solution = self._solve()
        result = docks.plan_docks(solution['schedule'], plan['trips'], self.params)
        self.assertEqual(len(result['entries']), len(plan['trips']))
        for entry in result['entries']:
            self.assertIsNotNone(entry['prep_start'])
            self.assertLessEqual(entry['prep_start'], entry['prep_end'])
            labels = [step['label'] for step in entry['timeline_text']]
            self.assertEqual(labels, ['备货开始', '备货完成', '车辆到达', '开始装车', '装车完成', '发车'])
            for step in entry['timeline_text']:
                self.assertTrue(step['time'] == '' or ':' in step['time'])

    def test_dock_utilization_and_summary(self):
        plan, solution = self._solve()
        result = docks.plan_docks(solution['schedule'], plan['trips'], self.params)
        self.assertGreater(len(result['docks']), 0)
        for dock in result['docks']:
            self.assertGreater(dock['trips'], 0)
            self.assertLessEqual(dock['utilization'], 100.0)
        self.assertEqual(result['summary']['trips'], len(plan['trips']))
        self.assertEqual(result['summary']['conflicts'], len(result['conflicts']))

    def test_conflict_detection_on_overlap(self):
        """人为造两个同月台重叠的装车时段，必须报出月台占用冲突。"""
        schedule = [
            {'trip_id': 'T1', 'vehicle_id': 'V1', 'dock_code': 'D1', 'prep_start': 200, 'prep_end': 230,
             'vehicle_arrival': 240, 'load_start': 240, 'load_end': 280, 'depart': 300, 'wait_min': 0},
            {'trip_id': 'T2', 'vehicle_id': 'V2', 'dock_code': 'D1', 'prep_start': 200, 'prep_end': 230,
             'vehicle_arrival': 260, 'load_start': 260, 'load_end': 300, 'depart': 320, 'wait_min': 0},
        ]
        trips = [{'trip_id': 'T1', 'batch_id': 'B1', 'customer_codes': ['1']},
                 {'trip_id': 'T2', 'batch_id': 'B1', 'customer_codes': ['2']}]
        result = docks.plan_docks(schedule, trips, self.params)
        types = {c['type'] for c in result['conflicts']}
        self.assertIn('月台占用冲突', types)
        self.assertEqual(result['summary']['errors'], 1)

    def test_prep_late_and_load_after_depart_detected(self):
        schedule = [{'trip_id': 'T1', 'vehicle_id': 'V1', 'dock_code': 'D2', 'prep_start': 300,
                     'prep_end': 360, 'vehicle_arrival': 320, 'load_start': 330, 'load_end': 400,
                     'depart': 390, 'wait_min': 10}]
        trips = [{'trip_id': 'T1', 'batch_id': 'B1', 'customer_codes': ['1']}]
        result = docks.plan_docks(schedule, trips, self.params)
        types = {c['type'] for c in result['conflicts']}
        self.assertIn('备货未完成即装车', types)
        self.assertIn('装车晚于发车', types)

    def test_vehicle_overlap_detected(self):
        schedule = [
            {'trip_id': 'T1', 'vehicle_id': 'V9', 'dock_code': 'D1', 'prep_start': 200, 'prep_end': 220,
             'vehicle_arrival': 230, 'load_start': 230, 'load_end': 260, 'depart': 270, 'wait_min': 0},
            {'trip_id': 'T2', 'vehicle_id': 'V9', 'dock_code': 'D2', 'prep_start': 200, 'prep_end': 220,
             'vehicle_arrival': 240, 'load_start': 250, 'load_end': 280, 'depart': 290, 'wait_min': 0},
        ]
        trips = [{'trip_id': 'T1', 'duration_min': 120}, {'trip_id': 'T2', 'duration_min': 120}]
        result = docks.plan_docks(schedule, trips, self.params)
        self.assertIn('车辆时间冲突', {c['type'] for c in result['conflicts']})

    def test_sub_minute_return_is_not_a_conflict(self):
        """求解器给的时长是小数小时：返场时刻与装车开始相差不足 1 分钟不算冲突。"""
        schedule = [
            {'trip_id': 'T1', 'vehicle_id': 'V1', 'dock_code': 'D1', 'prep_start': 200, 'prep_end': 230,
             'vehicle_arrival': 240, 'load_start': 240, 'load_end': 280, 'depart': 300, 'wait_min': 0},
            {'trip_id': 'T2', 'vehicle_id': 'V1', 'dock_code': 'D2', 'prep_start': 300, 'prep_end': 330,
             'vehicle_arrival': 340, 'load_start': 355, 'load_end': 395, 'depart': 410, 'wait_min': 0},
        ]
        trips = [{'trip_id': 'T1', 'duration_min': 24.5}, {'trip_id': 'T2', 'duration_min': 60}]
        # T1：发车 300 + 时长 24.5 + 周转 30 = 354.5 返场，T2 在 355 装车 → 差 0.5 分钟，不应报冲突
        result = docks.plan_docks(schedule, trips, self.params)
        self.assertNotIn('车辆时间冲突', {c['type'] for c in result['conflicts']})

    def test_cpsat_schedule_has_no_dock_conflict(self):
        """CP-SAT 带月台不重叠约束，因此不应出现月台占用冲突。"""
        try:
            plan, solution = self._solve(solver='cpsat')
        except Exception:
            self.skipTest('CP-SAT 不可用')
        result = docks.plan_docks(solution['schedule'], plan['trips'], self.params)
        self.assertNotIn('月台占用冲突', {c['type'] for c in result['conflicts']})


class TestChargingPlan(DocksAndChargingCase):
    def test_sessions_within_valley_and_no_overlap(self):
        plan, solution = self._solve()
        result = charging.plan_charging(plan['vehicles'], solution['schedule'], self.params)
        ev_codes = {v['code'] for v in plan['vehicles'] if v['energy_type'] == '纯电'}
        self.assertEqual({s['vehicle_code'] for s in result['sessions']}, ev_codes)
        for session in result['sessions']:
            self.assertGreaterEqual(session['start_min'], charging.VALLEY_START)
            if session['in_valley']:
                self.assertLessEqual(session['end_min'], charging.VALLEY_END)
            else:
                self.assertIn('谷段溢出', {c['type'] for c in result['conflicts']})
            self.assertEqual(session['end_min'] - session['start_min'], charging.SESSION_MINUTES)
            self.assertGreater(session['energy_kwh'], 0)
        self.assertNotIn('桩位冲突', {c['type'] for c in result['conflicts']})
        self.assertEqual(result['summary']['sessions'], len(ev_codes))
        self.assertGreater(result['summary']['total_cost'], 0)

    def test_more_evs_than_charger_capacity(self):
        """纯电车数量超过"3 桩 × 谷段"的排班能力时，必须报出冲突而不是静默排满。"""
        vehicles = [{'code': f'E{i}', 'energy_type': '纯电', 'effective_range_km': 200} for i in range(1, 25)]
        schedule = [{'trip_id': f'T{i}', 'vehicle_id': f'E{i}', 'depart': 300} for i in range(1, 25)]
        result = charging.plan_charging(vehicles, schedule, self.params)
        self.assertTrue(result['conflicts'])
        self.assertIn('谷段溢出', {c['type'] for c in result['conflicts']})
        # 谷段容量 = 3 桩 × 360 分钟 / 45 分钟 = 24 个时段，但发车时刻集中时排不下全部车辆
        self.assertGreaterEqual(result['summary']['in_valley_sessions'], 6)
        self.assertEqual(result['summary']['sessions'], 24)

    def test_range_violation_reported(self):
        vehicles = [{'code': 'E1', 'energy_type': '纯电', 'effective_range_km': 100}]
        schedule = [{'trip_id': 'T1', 'vehicle_id': 'E1', 'depart': 300, 'distance_km': 150}]
        result = charging.plan_charging(vehicles, schedule, self.params)
        self.assertIn('续航不足', {c['type'] for c in result['conflicts']})

    def test_charge_before_departure(self):
        plan, solution = self._solve()
        result = charging.plan_charging(plan['vehicles'], solution['schedule'], self.params)
        depart_by_vehicle = {}
        for entry in solution['schedule']:
            code = entry['vehicle_id']
            depart_by_vehicle[code] = min(depart_by_vehicle.get(code, 10 ** 9), int(entry['depart']))
        for session in result['sessions']:
            depart = depart_by_vehicle.get(session['vehicle_code'])
            if depart is not None:
                self.assertLessEqual(session['end_min'], depart,
                                     f"{session['vehicle_code']} 充电晚于发车")


if __name__ == '__main__':
    unittest.main()
