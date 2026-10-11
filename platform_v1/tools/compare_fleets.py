"""对比三种车队口径下的 S1/S3 结果，回答"C3 到底几辆车"的口径问题。"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import costing, planning  # noqa: E402
from simulation.scheduler import solve_scenario  # noqa: E402

tmp = tempfile.TemporaryDirectory()
settings = build_settings({'PLATFORM_ROOT': str(BASE), 'PLATFORM_DB': str(Path(tmp.name) / 'p.db')})
auth.bootstrap(settings)
conn = db.connect(settings)
for fleet in ('baseline', 'c3', 'c3_10'):
    rows = list(conn.execute(
        'SELECT energy_type, vehicle_type, COUNT(*) c FROM vehicles WHERE fleet_tag=? GROUP BY 1,2', (fleet,)))
    print(f"{fleet}: 共 {sum(r['c'] for r in rows)} 辆 " +
          str({r['energy_type'] + '/' + r['vehicle_type']: r['c'] for r in rows}))

products = planning.load_products(conn)
params = planning.load_params(conn)
print('')
for scope in ('姜蒜', '鲜面条'):
    for fleet in ('baseline', 'c3', 'c3_10'):
        plan = planning.build_plan(conn, scope, fleet)
        solution = solve_scenario('D3', plan['solver_data'], {'solver': 'heuristic', 'wait_min': 18})
        kpi = costing.price_plan(plan, products, params, solution['schedule'])
        used = {t['vehicle_code'] for t in plan['trips'] if t.get('vehicle_code')}
        print(f"{scope:4s} {fleet:9s} 用车 {len(used):2d} 趟次 {kpi['trips']:2d} "
              f"成本 {kpi['total_cost']:8.2f} 元 里程 {kpi['total_distance_km']:7.2f} km "
              f"碳排 {kpi['carbon_kg']:7.2f} kg 准时 {kpi['on_time_rate']}%")
conn.close()
