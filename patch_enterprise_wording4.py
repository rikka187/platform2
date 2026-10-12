"""第四轮（收尾）：清掉最后几处注释里的案例/论文/表格引用。"""
import pathlib

ROOT = pathlib.Path(r'D:\platform2\platform_v1')

REPLACEMENTS = [
    ('simulation/planning.py', '``distance_km`` 给出时按第三章口径做**新能源可行路线判定**：纯电车的',
     '``distance_km`` 给出时做**新能源可行路线判定**：纯电车的'),
    ('simulation/planning.py', '# 批次起送时刻（表2-3/2-4 的"批次一 04:30 起送"），由 build_plan 注入',
     '# 批次起送时刻（如"批次一 04:30 起送"），由 build_plan 注入'),
    ('simulation/planning.py', '"""新能源可行路线判定（第三章 3.4.2）：纯电有效续航必须覆盖本趟里程。"""',
     '"""新能源可行路线判定：纯电有效续航必须覆盖本趟里程。"""'),
    ('simulation/planning.py', '2. 优先复用"已经返回、可以再跑一趟"的车（对应论文的多趟次复用），',
     '2. 优先复用"已经返回、可以再跑一趟"的车（多趟次复用），'),
    ('simulation/planning.py', '与团队求解程序的口径一致（"案例批次时间\'X 时及以后发车\'即就绪时间，允许延后"）：',
     '口径为"批次时间\'X 时及以后发车\'即就绪时间，允许延后"：'),
    ('simulation/planning.py', '# 本品类各批次的最早发车时刻（表2-3/2-4 的"批次 N（HH:MM 起送）"）',
     '# 本品类各批次的最早发车时刻（"批次 N（HH:MM 起送）"）'),
    ('simulation/scenarios.py', '# 情景车队口径：C3/D3 在可行时优先纯电，两条长线保留燃油（第三章口径）。',
     '# 情景车队口径：C3/D3 在可行时优先纯电，两条长线保留燃油。'),
    ('simulation/scoring.py', '"""多情景五维评分（论文 4.7.3 评价指标体系）。',
     '"""多情景五维评分（经济 / 时效 / 绿色 / 协同 / 效率）。'),
    ('simulation/vrp_optimizer.py', '"""论文口径的路径优化求解器：Clarke-Wright 节约法 + ALNS + 车辆区间调度。',
     '"""路径优化求解器：Clarke-Wright 节约法 + ALNS + 车辆区间调度。'),
    ('simulation/vrp_optimizer.py', '**移植自团队原始求解程序**（`vrp_solver.py`，对应论文 2.4「求解算法与算例设置」），',
     '算法结构与约束来自团队既有的路径优化程序（`vrp_solver.py`），'),
    ('simulation/vrp_optimizer.py', '目标函数（论文 F1 + F2）', '目标函数（运营成本 + 时效损失）'),
    ('simulation/vrp_optimizer.py', '案例未给碳排参数，F3 不计入目标。**注意：本口径不含维护费、货损与能耗**，',
     '碳排暂不计入目标函数。**注意：本口径不含维护费、货损与能耗**，'),
    ('simulation/vrp_optimizer.py', '与论文 S0 的 4226.68 元可比；平台扩展口径（含维护/货损/能耗/碳排）由 costing 另算。',
     '与"配送成本"口径一致；平台综合口径（含维护/货损/能耗/碳排）由 costing 另算。'),
]

changed, missed = 0, []
for rel, old, new in REPLACEMENTS:
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    if old not in text:
        missed.append(f'{rel}: {old[:50]}')
        continue
    path.write_text(text.replace(old, new), encoding='utf-8')
    changed += 1
print(f'第四轮替换 {changed} 处')
for item in missed:
    print('  未匹配：' + item)
