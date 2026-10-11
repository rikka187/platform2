"""第三章口径标定：C0 / C3 / C4 的日全成本与碳排，对照论文
C0 1876.51 元/日、C3 1738.33 元/日（−7.4%）、C3 碳排 135.95 kg（−52.2%）、C4 1702.68 元/日（−64.1% 减排）。
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'platform_v1'
sys.path.insert(0, str(BASE))
from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import joint_plan, lifecycle, planning, vrp_optimizer  # noqa: E402

profile = sys.argv[1] if len(sys.argv) > 1 else 'paper'
restarts = int(sys.argv[2]) if len(sys.argv) > 2 else 2
iterations = int(sys.argv[3]) if len(sys.argv) > 3 else 600

tmp = tempfile.TemporaryDirectory()
settings = build_settings({'PLATFORM_ROOT': str(BASE), 'PLATFORM_DB': str(Path(tmp.name) / 'p.db')})
auth.bootstrap(settings)
conn = db.connect(settings)
params = planning.load_params(conn)
params.update({k: v for k, v in lifecycle.DEFAULTS.items() if k not in params})

print(f'算例口径 {profile}｜ALNS restarts={restarts} iterations={iterations}\n')
results = {}
for name, spec in lifecycle.COMPARE_SCENARIOS.items():
    fleet = spec['fleet_tag']
    vehicles = [dict(r) for r in conn.execute(
        'SELECT code, vehicle_type, energy_type, purchase_price_wan, maintenance_cost '
        'FROM vehicles WHERE fleet_tag=? AND available=1 ORDER BY vehicle_type, code', (fleet,))]
    if not vehicles:
        print(f'{name}: 车队 {fleet} 无车辆，跳过')
        continue
    plan = joint_plan.build_joint_plan(conn, fleet_tag=fleet, profile=profile,
                                       restarts=restarts, iterations=iterations)
    workload = lifecycle.workload_from_trips(plan['trips'])
    evaluation = lifecycle.evaluate_fleet(vehicles, workload, params)
    results[name] = {'plan': plan, 'evaluation': evaluation, 'spec': spec}
    s = plan['alns_summary']
    print(f"{name} {spec['label']}")
    print(f"    车辆 {len(vehicles)} 辆（纯电 {sum(1 for v in vehicles if v['energy_type'] == '纯电')}）"
          f"｜趟次 {s['trips']}｜里程 {s['total_distance_km']} km")
    print(f"    日全成本 {evaluation['daily_cost']} 元 = 折旧 {evaluation['breakdown']['amortization']}"
          f" + 能源 {evaluation['breakdown']['energy_cost']} + 维护 {evaluation['breakdown']['maintenance']}"
          f" + 年费 {evaluation['breakdown']['fixed_annual']}")
    print(f"    碳排 {evaluation['carbon_kg']} kg｜分能源 {evaluation['by_energy']}")
    print()

targets = {'C0': 1876.51, 'C3': 1738.33, 'C4': 1702.68}
print('对照论文：')
if 'C0' in results:
    base = results['C0']['evaluation']
    for name, item in results.items():
        cost = item['evaluation']['daily_cost']
        carbon = item['evaluation']['carbon_kg']
        target = targets.get(name)
        delta = f'{cost - target:+.2f} 元（{(cost - target) / target * 100:+.1f}%）' if target else '-'
        cut = ((base['carbon_kg'] - carbon) / base['carbon_kg'] * 100) if base['carbon_kg'] else 0
        print(f"   {name}: 日全成本 {cost} 元（论文 {target}，差 {delta}）｜碳排 {carbon} kg"
              f"（相对 C0 减排 {cut:.1f}%）")
conn.close()
