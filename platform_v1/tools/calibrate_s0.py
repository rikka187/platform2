"""用论文 S0 的目标值校准移植后的求解器。

论文 S0（第二章路径优化基准方案）：日总配送成本 4226.68 元、总里程 717.16 km、
使用 7 辆车、8 个趟次、期望时间窗准时率 75.0%。
"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'platform_v1'
sys.path.insert(0, str(BASE))
from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402
from simulation import vrp_optimizer as vrp  # noqa: E402

restarts = int(sys.argv[1]) if len(sys.argv) > 1 else 1
profile = sys.argv[4] if len(sys.argv) > 4 else 'case2'
iterations = int(sys.argv[2]) if len(sys.argv) > 2 else 500
fleet = sys.argv[3] if len(sys.argv) > 3 else 'baseline'

tmp = tempfile.TemporaryDirectory()
settings = build_settings({'PLATFORM_ROOT': str(BASE), 'PLATFORM_DB': str(Path(tmp.name) / 'p.db')})
auth.bootstrap(settings)
conn = db.connect(settings)

problem = vrp.build_problem(conn, fleet_tag=fleet, profile=profile)
print(f'算例口径：{profile}')
print(f'问题规模：' + '，'.join(f'{p} {len(problem.nodes[p])} 户' for p in problem.products))
print(f'车辆池：' + '，'.join(
    f"{name} {spec['count']} 辆（{spec['capacity']:.0f} kg / {spec['volume']:.0f} m³，出车 {spec['fixed']:.0f} 元）"
    for name, spec in problem.vehicles.items()))

started = time.time()
result = vrp.solve(problem, restarts=restarts, iterations=iterations)
elapsed = time.time() - started
s = result['summary']
print(f'\n求解耗时 {elapsed:.1f} 秒（restarts={restarts}, iterations={iterations}, seed={vrp.DEFAULT_SEED}）')
print(f"趟次 {s['trips']} 个（复用 {s['reused_trips']} 趟）｜用车 {s['vehicles_used']} 辆｜"
      f"里程 {s['total_distance_km']} km｜准时率 {s['on_time_rate']}%"
      f"（{s['on_time_customers']}/{s['customers']}）")
print(f"日总配送成本 {s['delivery_cost']} 元 = 出车 {s['cost_fixed']} + 运输 {s['cost_transport']} "
      f"+ 配送制冷 {s['cost_cool_delivery']} + 服务制冷 {s['cost_cool_service']} "
      f"+ 早到 {s['cost_early']} + 晚到 {s['cost_late']}")
print(f"平均装载率 {s['avg_load_rate']}%｜合计装载 {s['total_kg']} kg / {s['total_m3']} m³")
for product, info in s['by_product'].items():
    print(f"   {product}：{info['trips']} 趟 {info['km']} km {info['cost']} 元")

print('\n各趟明细：')
for t in result['trips']:
    seq = ' → '.join(f"{x['code']}({int(x['service_start_min']) // 60:02d}:{int(x['service_start_min']) % 60:02d})"
                     for x in t['sequence'])
    print(f"   {t['trip_id']:16s} {t['vehicle_code']:6s} {t['energy_type']:2s} "
          f"发车{int(t['planned_departure_min']) // 60:02d}:{int(t['planned_departure_min']) % 60:02d} "
          f"装载{t['load_kg']:7.0f}kg {t['load_rate']:5.1f}% {t['distance_km']:7.2f}km "
          f"成本{t['cost']['total']:8.2f} | {seq}")

print('\n与论文 S0 目标对比：')
targets = [('趟次', s['trips'], 8), ('用车', s['vehicles_used'], 7),
           ('里程 km', s['total_distance_km'], 717.16), ('日总配送成本 元', s['delivery_cost'], 4226.68),
           ('准时率 %', s['on_time_rate'], 75.0)]
for label, actual, target in targets:
    delta = actual - target
    pct = (delta / target * 100) if target else 0
    print(f'   {label:16s} 本平台 {actual:>9} | 论文 {target:>9} | 差 {delta:+.2f}（{pct:+.1f}%）')
conn.close()
