"""第三轮：清理 Python 模块注释/文档串里的案例、表格、论文引用（界面已清完）。"""
import pathlib

ROOT = pathlib.Path(r'D:\platform2\platform_v1')

REPLACEMENTS = [
    ('server.py', '# 建库建表 + 首次导入案例2 种子 + 创建默认管理员', '# 建库建表 + 首次导入基础数据 + 创建默认管理员'),
    ('wsgi.py', '导入案例2 种子数据', '导入基础数据'),
    ('app/auth.py', '对应论文 4.3.5"权限控制', '权限控制'),
    ('app/auth.py', '首次导入案例2 种子', '首次导入基础数据'),
    ('app/db.py', 'SQLite 库结构、连接管理、案例2 种子数据导入。', 'SQLite 库结构、连接管理、基础数据导入。'),
    ('app/db.py', '建表 + 首次导入案例2 种子数据。', '建表 + 首次导入基础数据。'),
    ('app/db.py', '# 第三章备选升级口径 C4：8 纯电 + 2 保留燃油（纯电比例高于 C3）',
     '# 备选升级口径 C4：8 纯电 + 2 保留燃油（纯电比例高于 C3）'),
    ('app/db.py', '能耗换成纯电口径（第三章：小型 0.42 kWh/km、中型 1.96 kWh/km），',
     '能耗换成纯电口径（小型 0.42 kWh/km、中型 1.96 kWh/km），'),
    ('simulation/charging.py', '"""新能源充电排班与校验（第三章 C3 口径）。', '"""新能源充电排班与校验（C3 配置口径）。'),
    ('simulation/costing.py', '"""成本与 KPI 核算（选题表2-5 / 表2-6 口径）。', '"""成本与 KPI 核算（平台成本口径）。'),
    ('simulation/costing.py', '本模块按选题给定的系数把方案**定价**', '本模块按参数表给定的系数把方案**定价**'),
    ('simulation/costing.py', '1. 运输成本 = Σ(出车固定成本 + 单位里程成本 × 里程)            （表2-5）',
     '1. 运输成本 = Σ(出车固定成本 + 单位里程成本 × 里程)'),
    ('simulation/costing.py', '2. 制冷成本 = 8.5 元/h × 行驶时间 + 10 元/h × 服务时间           （表2-6）',
     '2. 制冷成本 = 8.5 元/h × 行驶时间 + 10 元/h × 服务时间'),
    ('simulation/costing.py', '3. 时间窗惩罚 = 5 元/h × 早到时间 + 15 元/h × 晚到时间           （表2-6，对标**期望窗**）',
     '3. 时间窗惩罚 = 5 元/h × 早到时间 + 15 元/h × 晚到时间（对标**期望窗**）'),
    ('simulation/costing.py', '4. 货损成本 = 配送货损率 0.008 × 货值 + 服务货损率 0.0016 × 货值 （表2-6）',
     '4. 货损成本 = 配送货损率 0.008 × 货值 + 服务货损率 0.0016 × 货值'),
    ('simulation/costing.py', '5. 碳排 = 燃油 2.63 kgCO₂/L × 油耗；纯电 0.1564 kgCO₂/kWh × 电耗（第三章）',
     '5. 碳排 = 燃油 2.63 kgCO₂/L × 油耗；纯电 0.1564 kgCO₂/kWh × 电耗'),
    ('simulation/costing.py', '# 各车型的能耗口径（第三章）', '# 各车型的能耗口径'),
    ('simulation/costing.py', '（表2-6：生姜 3.0、大蒜 3.2、鲜面条 3.5）', '（生姜 3.0、大蒜 3.2、鲜面条 3.5）'),
    ('simulation/costing.py', '返回 KPI（与论文 4.7.2 的指标口径一致）。', '返回 KPI（与平台评价指标口径一致）。'),
    ('simulation/cpsat_scheduler.py', '# 第三章口径：C3/D3 中两条长线', '# C3/D3 中两条长线'),
    ('simulation/geo.py', '口径（与选题表2-3/2-4 的经纬度一致）', '口径（与业务主数据的经纬度一致）'),
    ('simulation/geo.py', '（车速默认取选题表2-6 的 40 km/h）', '（车速默认 40 km/h）'),
    ('simulation/geo.py', '反向验证本实现，避免"平台算的距离和论文里的距离对不上"。',
     '反向验证本实现，避免"平台算的距离与既有矩阵对不上"。'),
    ('simulation/geo.py', '# 校核方法：用案例2 的经纬度按 Haversine 重算 31×31 矩阵，与',
     '# 校核方法：用业务主数据的经纬度按 Haversine 重算 31×31 矩阵，与'),
    ('simulation/geo.py', '# 因此平台统一使用 6371.0，保证与论文/报告里的距离数字完全一致。',
     '# 因此平台统一使用 6371.0，保证与既有距离矩阵完全一致。'),
    ('simulation/planning.py', '（把案例2 的客户点转成现有求解器能吃的输入）', '（把客户点转成求解器输入）'),
    ('simulation/planning.py', '（选题表2-6，40 km/h）', '（40 km/h）'),
    ('simulation/planning.py', '* 批次归属来自表2-3/表2-4 的"批次归属"列，起送时刻即该批次的最早发车时间。',
     '* 批次归属取自客户主数据，起送时刻即该批次的最早发车时间。'),
    ('simulation/planning.py', '（选题：装车高峰 03:00-07:00）', '（装车高峰 03:00-07:00）'),
    ('app/api_monitor.py', '便于测试与复现（不使用随机数）', '便于测试与回溯（不使用随机数）'),
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
print(f'第三轮替换 {changed} 处')
for item in missed:
    print('  未匹配：' + item)
