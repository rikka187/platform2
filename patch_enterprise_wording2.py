"""第二轮：清理导出报表、接口提示与代码注释里的案例/表格/论文引用。"""
import pathlib

ROOT = pathlib.Path(r'D:\platform2\platform_v1')

REPLACEMENTS = [
    # 参数说明漏掉的一处
    ('app/labels.py', "('单桩功率', 'kW', '第三章配置')", "('单桩功率', 'kW', '充电站配置')"),
    # 接口可见提示与导出报表口径列
    ('app/api_plan.py', "'求解器只能是 auto / heuristic / cpsat / alns（论文口径）。'",
     "'求解器只能是 auto / heuristic / cpsat / alns（智能优化）。'"),
    ('app/api_plan.py', "'论文 4.5.4.2'),", "'调度情景'),"),
    ('app/api_plan.py', "'表2-5/2-6 口径合计'", "'成本口径合计'"),
    ('app/api_plan.py', "('时间窗惩罚(元)', kpi.get('penalty_cost'), '早到 5 元/h + 晚到 15 元/h'),",
     "('时间窗惩罚(元)', kpi.get('penalty_cost'), '早到 5 元/h + 晚到 15 元/h'),"),
    # 注释与文档串（内部也能一眼看懂，不再提案例/论文）
    ('app/api_plan.py', '客户→批次→趟次 → 现有求解器 → 选题口径 KPI，并把方案落库。',
     '客户→批次→趟次 → 路径优化求解器 → 成本核算 KPI，并把方案落库。'),
    ('app/api_plan.py', '情景（论文 4.5.4.2）', '调度情景'),
    ('app/api_plan.py', '按选题表2-5/2-6 定价、落库与回放。', '按平台成本口径定价、落库与回放。'),
    ('app/api_plan.py', '# 论文口径求解器：Clarke-Wright + ALNS + 车辆区间调度（移植自团队原始程序）',
     '# 智能优化求解器：Clarke-Wright 节约法 + ALNS + 车辆区间调度'),
    ('app/api_plan.py', '# 注意：ALNS（论文口径）输出的已经是优化后里程，不能再叠加人工绕行系数。',
     '# 注意：ALNS（智能优化）输出的已经是优化后里程，不能再叠加人工绕行系数。'),
    ('app/api_plan.py', '# 论文 F1+F2 口径的日总配送成本，作为与论文对照的头条指标',
     '# 配送成本口径的日总配送成本，作为方案头条指标'),
    ('app/api_plan.py', '# 求解器口径的原始指标一并保留，便于与论文对照',
     '# 求解器原始指标一并保留，便于核对'),
    ('app/api_plan.py', '"""第三章口径：C0 / C3 / C4 车队配置的日全成本与碳排对比。',
     '"""车队配置对比：C0 / C3 / C4 的日全成本与碳排。'),
    ('app/api_plan.py', '# 第三章口径的关键：**同一组配送任务、只换车队配置**',
     '# 对比口径的关键：**同一组配送任务、只换车队配置**'),
    ('app/api_plan.py', '每个车队各跑一次 ALNS（默认快速档）', '每个车队各跑一次智能优化（默认快速档）'),
    # 第三章口径模块
    ('simulation/lifecycle.py', '"""第三章口径：车队"日全成本"与碳排（新能源配置推荐 C0 / C3 / C4）。',
     '"""车队全生命周期"日全成本"与碳排（新能源配置 C0 / C3 / C4）。'),
    ('simulation/lifecycle.py', '口径构成（与论文第三章一致的四项）', '口径构成（四项）'),
    ('simulation/lifecycle.py', '# 能耗强度（与第三章表口径一致；缺省用平台 params 里的车辆单耗）',
     '# 能耗强度（缺省取平台 params 里的车辆单耗，可在「参数设置」调整）'),
    ('simulation/lifecycle.py', '# 为对齐论文第三章公开数字所做的假设（团队可用第三章参数表逐项替换）',
     '# 成本口径假设（可在「参数设置」中逐项替换）'),
    ('simulation/lifecycle.py', '（第三章口径的关键：任务不变、只换配置）', '（任务不变、只换配置）'),
    ('simulation/lifecycle.py', '参数全部来自 ``params`` 表，可在「参数设置」页调整；``CALIBRATION`` 记录为对齐论文',
     '参数全部来自 ``params`` 表，可在「参数设置」页调整；``CALIBRATION`` 记录当前'),
    ('simulation/lifecycle.py', '第三章公开数字（C0 1876.51 元/日、C3 1738.33 元/日、C3 碳排 135.95 kg）所做的假设，',
     '默认假设（口径可逐项替换），'),
    ('simulation/lifecycle.py', '便于团队用第三章参数表逐项核对。', '便于按实际运营口径逐项核对。'),
    # 联合方案模块
    ('simulation/joint_plan.py', '"""联合方案：用论文口径的 ALNS 求解器生成"客户→批次→趟次"方案，并对接平台既有模块。',
     '"""联合方案：用智能优化（节约法 + ALNS）求解器生成"客户→批次→趟次"方案，并对接平台既有模块。'),
    ('simulation/joint_plan.py', '（论文 F1+F2 口径的日总配送成本与准时率）', '（配送成本口径的日总配送成本与准时率）'),
    # 监控模块
    ('app/api_monitor.py', '（选题 4.6.2）', ''),
]

changed, missed = 0, []
for rel, old, new in REPLACEMENTS:
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    if old not in text:
        missed.append(f'{rel}: {old[:56]}')
        continue
    path.write_text(text.replace(old, new), encoding='utf-8')
    changed += 1
print(f'第二轮替换 {changed} 处')
for item in missed:
    print('  未匹配：' + item)
