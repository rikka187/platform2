"""把团队原始求解器里的"基准构造算例"导出为平台数据文件。

团队脚本（vrp_solver.py）顶部说明："客户逐日明细案例未公开，此为基准构造"——
论文 S0 的 4226.68 元 / 717.16 km / 8 趟次 / 75.0% 就是用这套构造窗口算出来的
（姜蒜批一可接受窗到 10:00，而表2-3 的真实逐客户窗口只到 06:00-06:30）。
平台把两套口径都保留：`case2`（表2-3/2-4 真实数据）与 `paper`（论文基准构造）。

用法：
    python tools/build_paper_profile.py [vrp_solver.py 路径]
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOLVER = Path.home() / 'Desktop' / '物流设计大赛' / 'vrp_solver.py'
OUT = ROOT / 'data' / 'case2' / 'customer_profile_paper.csv'
BATCH_NAME = {1: '批次一', 2: '批次二'}
DEPART = {('姜蒜', '批次一'): '04:30', ('姜蒜', '批次二'): '06:30',
          ('鲜面条', '批次一'): '04:30', ('鲜面条', '批次二'): '05:00'}


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location('ref_solver', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['ref_solver'] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOLVER
    if not path.is_file():
        print(f'[错误] 找不到原始求解器：{path}')
        return 1
    ref = load_reference(path)
    rows = []
    for product in ('姜蒜', '鲜面条'):
        for code in sorted(ref.CUSTOMERS[product]):
            item = ref.CUSTOMERS[product][code]
            rows.append({
                'product_scope': product,
                'code': code,
                'batch_code': BATCH_NAME[item['batch']],
                'depart_after': DEPART[(product, BATCH_NAME[item['batch']])],
                'demand_kg': item['demand'],
                'service_min': item['service'],
                'exp_start': item['e'], 'exp_end': item['l'],
                'acc_start': item['E'], 'acc_end': item['L'],
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    total = {}
    for row in rows:
        key = (row['product_scope'], row['batch_code'])
        total[key] = total.get(key, 0) + row['demand_kg']
    print(f'已写出 {OUT}（{len(rows)} 行）')
    for key, value in sorted(total.items()):
        print(f'   {key[0]} {key[1]}：合计 {value} kg')
    return 0


if __name__ == '__main__':
    sys.exit(main())
