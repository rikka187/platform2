"""把界面上与"案例 / 表2-x / 论文 / 第三章"相关的文字改成企业平台措辞。"""
import pathlib

ROOT = pathlib.Path(r'D:\platform2\platform_v1')

REPLACEMENTS = [
    # 客户管理
    ('app/static/customers.html',
     "'选题案例2 表2-3 / 表2-4：坐标、需求量、期望与可接受时间窗、服务时间、批次归属');",
     "'客户主数据：坐标、需求量、期望与可接受时间窗、服务时间、批次归属');"),
    ('app/static/customers.html', "<small>表2-3</small>", "<small>姜蒜业务</small>"),
    ('app/static/customers.html', "<small>表2-4</small>", "<small>鲜面条业务</small>"),
    # 主页
    ('app/static/index.html',
     "选题案例 2 · 姜蒜与鲜面条城配协同调度 · 银犁仓库（104.26, 30.85）",
     "城市冷链仓配协同调度 · 中央配送中心（104.26, 30.85）"),
    ('app/static/index.html', "成本口径 表2-5 / 表2-6", "成本口径 出车 + 里程 + 制冷 + 时间窗"),
    ('app/static/index.html', "'表2-3/2-4 客户、时间窗、批次与距离校核'", "'客户主数据、时间窗、批次与距离校核'"),
    ('app/static/index.html', "'表2-5/2-6 车辆参数与三种车队口径'", "'车辆参数与车队口径配置'"),
    # 登录页
    ('app/static/login.html', "选题案例 2 · 姜蒜与鲜面条城配协同调度", "城市冷链仓配一体化协同调度"),
    # 参数设置
    ('app/static/params.html',
     "'选题表2-5 / 表2-6 与第三章能源碳排参数；修改即写入留痕日志');",
     "'成本系数、车辆与能源碳排参数；修改即写入留痕日志');"),
    # 方案工作台
    ('app/static/plan.html',
     "'方案工作台：客户→批次→趟次预处理 + 平台现有求解器（启发式/CP-SAT）+ 选题表2-5/2-6 成本口径 · 地图/月台/充电/导出都在本页');",
     "'方案工作台：客户→批次→趟次 + 路径优化（启发式 / CP-SAT / 智能优化）+ 成本核算 · 地图、月台、充电、导出都在本页');"),
    ('app/static/plan.html', "<label>情景（论文 4.5.4.2）</label>", "<label>调度情景</label>"),
    ('app/static/plan.html', "论文口径 ALNS（节约法 + 自适应大邻域）", "智能路径优化（节约法 + 自适应大邻域搜索）"),
    ('app/static/plan.html', "论文基准构造（复现 S0 4226.68 元）", "标准算例（基准口径）"),
    ('app/static/plan.html', ">表2-3/2-4 真实窗口</option>", ">实际业务数据</option>"),
    ('app/static/plan.html', "<h3>成本构成（选题表2-5/2-6）</h3>", "<h3>成本构成</h3>"),
    ('app/static/plan.html', "<label>算例口径</label>", "<label>数据口径</label>"),
    ('app/static/plan.html',
     "+ `${r.profile ? ' · 算例口径 ' + (r.profile === 'paper' ? '论文基准构造' : '表2-3/2-4 真实窗口') : ''}`;",
     "+ `${r.profile ? ' · 数据口径 ' + (r.profile === 'paper' ? '标准算例' : '实际业务数据') : ''}`;"),
    ('app/static/plan.html', "论文 F1+F2 口径：出车 + 里程 + 制冷 + 时间窗惩罚",
     "配送成本口径：出车 + 里程 + 制冷 + 时间窗惩罚"),
    ('app/static/plan.html', "<b>扩展口径总成本</b>", "<b>综合成本</b>"),
    # 多情景评价
    ('app/static/scenarios.html',
     "el('matrixHint').textContent = '口径：选题表2-5/2-6 成本 + Haversine 里程；S0 含 1.22 人工调度绕行系数';",
     "el('matrixHint').textContent = '口径：配送成本（出车 + 里程 + 制冷 + 时间窗惩罚）+ Haversine 里程；S0 含 1.22 人工调度绕行系数';"),
    # 侧边栏页脚
    ('app/static/shell.js', "选题案例2口径 · Haversine 距离<br>", "距离口径 Haversine · 半径 6371.0 km<br>"),
    # 用户与留痕
    ('app/static/users.html',
     "'按角色分配读写权限，关键操作全部留痕（论文 4.3.5 权限控制）');",
     "'按角色分配读写权限，关键操作全部留痕');"),
    # 车辆管理
    ('app/static/vehicles.html',
     "'选题案例2 表2-5 / 表2-6：车型、载重、容积、成本、能耗；可增删车辆并自定义参数');",
     "'车辆主数据：车型、载重、容积、成本、能耗；可增删车辆并自定义参数');"),
    ('app/static/vehicles.html', "（选题表2-5）。", "。"),
    # 标签层：参数说明与求解器名称
    ('app/labels.py', "'表2-2；行驶时间 = 里程 ÷ 该速度'", "'行驶时间 = 里程 ÷ 该速度'"),
    ('app/labels.py', "'第三章配置：3 × 120 kW'", "'充电站配置：3 × 120 kW'"),
    ('app/labels.py', "'个', '第三章配置'", "'个', '充电站配置'"),
    ('app/labels.py', "'第三章车辆全生命周期摊销'", "'车辆全生命周期摊销'"),
    ('app/labels.py', "'表2-6 口径'", "'燃料价格口径'"),
    ('app/labels.py', "'第三章能耗口径'", "'能耗口径'"),
    ('app/labels.py', "'表2-5“固定成本”按每趟计'", "'按每趟计'"),
    ('app/labels.py', "'元/km', '表2-5'", "'元/km', '车辆成本口径'"),
    ('app/labels.py', "'表2-1'", "'车辆参数'"),
    ('app/labels.py', "'表2-6；对标期望时间窗'", "'对标期望时间窗'"),
    ('app/labels.py', "'第三章充电成本（排班只排谷段）'", "'充电成本（排班只排谷段）'"),
    ('app/labels.py', "'第三章充电成本'", "'充电成本'"),
    ('app/labels.py', "'第三章资产摊销'", "'资产摊销'"),
    ('app/labels.py', "'表2-6：行驶 + 等待'", "'行驶 + 等待'"),
    ('app/labels.py', "'表2-6：卸货服务期间'", "'卸货服务期间'"),
    ('app/labels.py', "('第三章资产口径',", "('资产与摊销',"),
    ('app/labels.py', "'alns': '论文口径 ALNS'", "'alns': '智能路径优化'"),
    ('app/labels.py', "'alns-paper': '论文口径 ALNS（论文基准算例）'", "'alns-paper': '智能路径优化（标准算例）'"),
    ('app/labels.py', "'alns-case2': '论文口径 ALNS（表2-3/2-4 真实窗口）'", "'alns-case2': '智能路径优化（实际业务数据）'"),
    # 接口可见提示
    ('app/api_plan.py', "'算例口径只能是 case2（表2-3/2-4 真实窗口）或 paper（论文基准构造）。'",
     "'数据口径只能是 case2（实际业务数据）或 paper（标准算例）。'"),
    # 第三章口径标定说明
    ('simulation/lifecycle.py', "'对齐论文第三章数字所用的假设：摊销年限 8 年、残值率 5%、'",
     "'成本口径假设（可在「参数设置」中调整）：摊销年限 8 年、残值率 5%、'"),
]

changed = 0
missed = []
for rel, old, new in REPLACEMENTS:
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    if old not in text:
        missed.append(f'{rel}: {old[:60]}')
        continue
    path.write_text(text.replace(old, new), encoding='utf-8')
    changed += 1
print(f'已替换 {changed} 处')
if missed:
    print('未匹配（需手动确认）：')
    for item in missed:
        print('  ' + item)
