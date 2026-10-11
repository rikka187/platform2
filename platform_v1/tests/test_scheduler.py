import unittest
from pathlib import Path

from simulation.scheduler import load_demo_data, solve_scenario, solver_status

ROOT = Path(__file__).resolve().parents[1]


class TestScheduler(unittest.TestCase):
    def test_demo_scenarios(self):
        data = load_demo_data(ROOT)
        for scenario in ['H0', 'S0', 'S1', 'C3', 'D3']:
            result = solve_scenario(scenario, data, {'wait_min': 20})
            self.assertGreater(len(result['schedule']), 0)
            self.assertIn('total_cost', result['kpi'])
            self.assertIn(result.get('solver'), {
                'heuristic', 'heuristic (CP-SAT fallback)', 'cp-sat'
            })

    def test_d3_reduces_wait(self):
        data = load_demo_data(ROOT)
        h0 = solve_scenario('H0', data, {'wait_min': 120, 'solver': 'heuristic'})
        d3 = solve_scenario('D3', data, {'wait_min': 18, 'solver': 'heuristic'})
        self.assertLessEqual(d3['kpi']['avg_wait_min'], h0['kpi']['avg_wait_min'])

    def test_solver_status_contract(self):
        status = solver_status()
        self.assertIn('heuristic', status['available'])
        self.assertIn('cp_sat_ready', status)

    @unittest.skipUnless(solver_status()['cp_sat_ready'], 'OR-Tools CP-SAT not installed')
    def test_cpsat_contract_when_available(self):
        data = load_demo_data(ROOT)
        result = solve_scenario('D3', data, {'solver': 'cpsat', 'max_time_seconds': 5})
        self.assertEqual(result['solver'], 'cp-sat')
        self.assertGreater(len(result['schedule']), 0)
        self.assertIn(result.get('solver_status'), {'OPTIMAL', 'FEASIBLE'})


if __name__ == '__main__':
    unittest.main()
